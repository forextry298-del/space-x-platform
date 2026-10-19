from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, secrets, string
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-deployment")
DB = os.environ.get("DATABASE_PATH", "space_x.db")
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            referral_code TEXT,
            referred_by INTEGER,
            created_at TEXT
        )
    """)

    # Safely upgrade existing databases without deleting user records.
    columns = {
        row["name"]
        for row in c.execute("PRAGMA table_info(users)").fetchall()
    }

    migrations = {
        "balance": "ALTER TABLE users ADD COLUMN balance REAL NOT NULL DEFAULT 0",
        "referral_code": "ALTER TABLE users ADD COLUMN referral_code TEXT",
        "referred_by": "ALTER TABLE users ADD COLUMN referred_by INTEGER",
        "created_at": "ALTER TABLE users ADD COLUMN created_at TEXT",
    }

    for name, sql in migrations.items():
        if name not in columns:
            c.execute(sql)
            print("Added missing users column:", name)

    # Create salary and machine purchase tables if missing.
    c.execute("""
        CREATE TABLE IF NOT EXISTS machine_purchases (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            machine_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            purchased_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS salary_claims (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            salary_month TEXT NOT NULL,
            amount REAL NOT NULL,
            claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, salary_month)
        )
    """)
    print("Salary and machine purchase tables verified.")
@app.route("/")
def index(): return redirect(url_for("register"))
@app.route("/register", methods=["GET","POST"])
def register():
    invite = request.args.get("ref", "").strip()
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        pw = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        code = request.form.get("captcha_input", "").strip()
        real = request.form.get("real_captcha", "").strip()
        invite = request.form.get("invite", "").strip()

        if not phone or not pw:
            flash("Phone number and password are required.", "error")
        elif pw != confirm:
            flash("Passwords do not match.", "error")
        elif len(pw) < 8:
            flash("Use at least 8 characters for your password.", "error")
        elif not code or code != real:
            flash("Incorrect verification code.", "error")
        else:
            try:
                with db() as c:
                    inviter = None
                    if invite:
                        inviter = c.execute(
                            "SELECT id FROM users WHERE referral_code=?",
                            (invite,)
                        ).fetchone()
                        if not inviter:
                            flash("Invalid invitation code.", "error")
                            real_captcha = ''.join(
                                secrets.choice(string.digits) for _ in range(4)
                            )
                            return render_template(
                                "register.html",
                                real_captcha=real_captcha,
                                invite=invite
                            )

                    c.execute(
                        """INSERT INTO users
                           (phone, password_hash, referred_by, created_at)
                           VALUES (?, ?, ?, datetime('now'))""",
                        (phone, generate_password_hash(pw),
                         inviter["id"] if inviter else None)
                    )
                    new_id = c.execute(
                        "SELECT id FROM users WHERE phone=?", (phone,)
                    ).fetchone()["id"]

                    referral_code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
                    c.execute(
                        "UPDATE users SET referral_code=? WHERE id=?",
                        (referral_code, new_id)
                    )

                flash("Account created. Please log in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Phone number already registered.", "error")

    real_captcha = ''.join(secrets.choice(string.digits) for _ in range(4))
    return render_template(
        "register.html",
        real_captcha=real_captcha,
        invite=invite
    )

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        with db() as c: u=c.execute("SELECT * FROM users WHERE phone=?",(request.form.get("phone","").strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"],request.form.get("password","")):
            session.clear(); session["uid"]=u["id"]; session["phone"]=u["phone"]; return redirect(url_for("dashboard"))
        flash("Incorrect phone number or password.","error")
    return render_template("login.html")
@app.route("/dashboard")
def dashboard():
    if "uid" not in session: return redirect(url_for("login"))
    return render_template("dashboard.html", phone=session.get("phone",""))



@app.route("/income")
def income():
    if "uid" not in session:
        return redirect(url_for("login"))
    return render_template("income.html")

@app.route("/profile")
def profile():
    from datetime import date, timedelta

    if "uid" not in session:
        return redirect(url_for("login"))

    today = date.today()
    first_this_month = today.replace(day=1)
    first_last_month = (first_this_month - timedelta(days=1)).replace(day=1)
    last_month = first_last_month.strftime("%Y-%m")

    with db() as c:
        user = c.execute(
            "SELECT * FROM users WHERE id=?", (session["uid"],)
        ).fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        invited_last = c.execute("""
            SELECT COUNT(*) FROM users
            WHERE referred_by=? AND created_at>=? AND created_at<?
        """, (user["id"], first_last_month.isoformat(),
              first_this_month.isoformat())).fetchone()[0]

        invited_this = c.execute("""
            SELECT COUNT(*) FROM users
            WHERE referred_by=? AND created_at>=?
        """, (user["id"], first_this_month.isoformat())).fetchone()[0]

        claim = c.execute("""
            SELECT amount FROM salary_claims
            WHERE user_id=? AND salary_month=?
        """, (user["id"], last_month)).fetchone()

        last_salary = claim["amount"] if claim else 0

    return render_template(
        "profile.html",
        user=user,
        balance=float(user["balance"] or 0),
        last_salary=last_salary,
        this_salary=0,
        invited_last=invited_last,
        invited_this=invited_this
    )


@app.route("/claim-salary", methods=["POST"])
def claim_salary():
    from datetime import date, datetime, timedelta

    if "uid" not in session:
        return redirect(url_for("login"))

    today = date.today()
    if today.day != 1:
        flash("Salary can only be claimed on the first day of each month.", "error")
        return redirect(url_for("profile"))

    # Calculate the previous calendar month.
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month
    last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
    start = last_month_start.isoformat()
    end = last_month_end.isoformat()
    salary_month = last_month_start.strftime("%Y-%m")

    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")

        # Count direct referrals registered last month who have a paid
        # purchase matching one of the inviter's paid machine purchases.
        row = con.execute("""
            SELECT COUNT(DISTINCT invited.id) AS qualifying_count
            FROM users AS invited
            WHERE invited.referred_by = ?
              AND invited.created_at >= ?
              AND invited.created_at < ?
              AND EXISTS (
                  SELECT 1
                  FROM machine_purchases AS their_purchase
                  JOIN machine_purchases AS my_purchase
                    ON my_purchase.machine_code = their_purchase.machine_code
                  WHERE their_purchase.user_id = invited.id
                    AND their_purchase.status = 'paid'
                    AND their_purchase.purchased_at >= ?
                    AND their_purchase.purchased_at < ?
                    AND my_purchase.user_id = ?
                    AND my_purchase.status = 'paid'
              )
        """, (session["uid"], start, end, start, end, session["uid"])).fetchone()

        qualifying_count = row["qualifying_count"] or 0
        amount = qualifying_count * 3000

        if amount <= 6000:
            con.rollback()
            flash(
                "You are not eligible. Your qualifying salary must be greater than UGX 6,000.",
                "error"
            )
            return redirect(url_for("profile"))

        # Unique(user_id, salary_month) prevents a second claim.
        con.execute("""
            INSERT INTO salary_claims (user_id, salary_month, amount)
            VALUES (?, ?, ?)
        """, (session["uid"], salary_month, amount))

        con.execute("""
            UPDATE users SET balance = COALESCE(balance, 0) + ?
            WHERE id = ?
        """, (amount, session["uid"]))

        con.commit()
        flash(f"Salary of UGX {amount:,.0f} credited to your Balance.", "success")
    except sqlite3.IntegrityError:
        con.rollback()
        flash("You have already claimed salary for this month.", "error")
    except Exception:
        con.rollback()
        app.logger.exception("Salary claim failed")
        flash("Salary could not be claimed. Please contact support.", "error")
    finally:
        con.close()

    return redirect(url_for("profile"))

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
