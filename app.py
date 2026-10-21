from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, secrets, string
from machine_catalog import MACHINE_CATALOG
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
        "wallet": "ALTER TABLE users ADD COLUMN wallet REAL NOT NULL DEFAULT 0",
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

    # Upgrade older machine purchase tables without deleting records.
    purchase_columns = {
        row["name"]
        for row in c.execute("PRAGMA table_info(machine_purchases)").fetchall()
    }
    purchase_migrations = {
        "price": "ALTER TABLE machine_purchases ADD COLUMN price REAL",
        "payment_source": "ALTER TABLE machine_purchases ADD COLUMN payment_source TEXT",
    }
    for name, sql in purchase_migrations.items():
        if name not in purchase_columns:
            c.execute(sql)
            print("Added missing machine_purchases column:", name)

    c.execute("""
        CREATE TABLE IF NOT EXISTS machine_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL REFERENCES machine_purchases(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            earning_day INTEGER NOT NULL,
            amount_usd REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(purchase_id, earning_day)
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

    c.execute("""
        CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            network TEXT NOT NULL,
            sender_phone TEXT NOT NULL,
            requested_ugx REAL NOT NULL,
            exchange_rate REAL NOT NULL,
            requested_usd REAL NOT NULL,
            transaction_id TEXT UNIQUE,
            paid_ugx REAL,
            paid_phone TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            submitted_at REAL,
            reviewed_at REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS referral_commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deposit_id INTEGER NOT NULL REFERENCES deposit_requests(id),
            beneficiary_user_id INTEGER NOT NULL REFERENCES users(id),
            referral_level INTEGER NOT NULL,
            amount_usd REAL NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(deposit_id, beneficiary_user_id, referral_level)
        )
    """)

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
                           (phone, password_hash, balance, referred_by, created_at)
                           VALUES (?, ?, 1.63, ?, datetime('now'))""",
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

                    if inviter:
                        c.execute(
                            """UPDATE users
                               SET balance = COALESCE(balance, 0) + 0.001
                               WHERE id = ?""",
                            (inviter["id"],)
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



@app.route("/ai")
def ai():
    if "uid" not in session:
        return redirect(url_for("login"))
    machines = [
        {
            "series": item[0],
            "name": name,
            "days": item[1],
            "price": item[2],
            "daily": item[3],
            "image": item[4],
        }
        for name, item in MACHINE_CATALOG.items()
    ]
    return render_template("ai.html", machines=machines)


@app.route("/income")
def income():
    from datetime import datetime, date

    if "uid" not in session:
        return redirect(url_for("login"))

    now = datetime.now()
    today = date.today()
    machines = []

    with db() as c:
        purchases = c.execute("""
            SELECT id, machine_code, purchased_at
            FROM machine_purchases
            WHERE user_id=? AND status='successful'
            ORDER BY id DESC
        """, (session["uid"],)).fetchall()

        for purchase in purchases:
            item = MACHINE_CATALOG.get(purchase["machine_code"])
            if not item:
                continue

            series, duration, price, daily, image = item
            try:
                purchased = datetime.fromisoformat(
                    str(purchase["purchased_at"]).replace("Z", "+00:00")
                )
                if purchased.tzinfo:
                    purchased = purchased.replace(tzinfo=None)
                elapsed = max(0, (today - purchased.date()).days)
            except (ValueError, TypeError):
                elapsed = 0

            completed = min(elapsed, duration)
            status = "Expired" if elapsed >= duration else "Running"

            earned = c.execute("""
                SELECT COALESCE(SUM(amount_usd), 0)
                FROM machine_earnings WHERE purchase_id=?
            """, (purchase["id"],)).fetchone()[0]

            machines.append({
                "id": purchase["id"],
                "name": purchase["machine_code"],
                "series": series,
                "days": duration,
                "completed": completed,
                "daily": daily,
                "total": round(daily * duration, 2),
                "earned": round(float(earned or 0), 2),
                "progress": round(completed * 100 / duration) if duration else 100,
                "status": status,
            })

    return render_template("income.html", machines=machines)

@app.route("/transactions")
def transactions():
    if "uid" not in session:
        return redirect(url_for("login"))

    with db() as c:
        purchases = c.execute(
            """SELECT machine_code AS title, price AS amount,
                      status, purchased_at AS created_at,
                      payment_source AS details
               FROM machine_purchases
               WHERE user_id = ?""",
            (session["uid"],)
        ).fetchall()

        claims = c.execute(
            """SELECT 'Salary claim' AS title, amount,
                      'paid' AS status, claimed_at AS created_at,
                      salary_month AS details
               FROM salary_claims
               WHERE user_id = ?""",
            (session["uid"],)
        ).fetchall()

        deposits = c.execute(
            """SELECT 'Mobile Money Deposit' AS title,
                      requested_usd AS amount, status,
                      datetime(created_at, 'unixepoch') AS created_at,
                      network AS details
               FROM deposit_requests WHERE user_id = ?""",
            (session["uid"],)
        ).fetchall()

    records = sorted(
        [dict(r) for r in purchases] + [dict(r) for r in claims] + [dict(r) for r in deposits],
        key=lambda r: r.get("created_at") or "",
        reverse=True
    )
    return render_template("transactions.html", transactions=records)


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
        wallet=float(user["wallet"] or 0),
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
        amount = round(qualifying_count * 0.7211, 2)

        if amount <= 1.44:
            con.rollback()
            flash(
                "You are not eligible. Your qualifying salary must be greater than $1.44 USD.",
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
        flash(f"Salary of ${amount:,.2f} USD credited to your Balance.", "success")
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



@app.route("/machine/<path:machine_code>")
def machine_detail(machine_code):
    if "uid" not in session:
        return redirect(url_for("login"))
    item = MACHINE_CATALOG.get(machine_code)
    if not item:
        return "Machine not found", 404
    series, days, price, daily, image = item
    machine = {"series": series, "days": days, "price": price,
               "daily": daily, "image": image}
    return render_template("machine_detail.html", name=machine_code,
                           machine=machine, total_income=daily * days)


@app.route("/purchase/<path:machine_code>", methods=["POST"])
def purchase_machine(machine_code):
    if "uid" not in session:
        return redirect(url_for("login"))

    item = MACHINE_CATALOG.get(machine_code)
    if not item:
        flash("Machine not found.", "error")
        return redirect(url_for("ai"))

    source = request.form.get("payment_source", "").lower()
    if source not in ("wallet", "balance"):
        flash("Choose Wallet or Balance.", "error")
        return redirect(url_for("machine_detail", machine_code=machine_code))

    _, _, price, _, _ = item
    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        user = con.execute(
            "SELECT id, balance, wallet FROM users WHERE id=?",
            (session["uid"],)
        ).fetchone()
        if not user:
            con.rollback()
            session.clear()
            return redirect(url_for("login"))

        funds = float(user[source] or 0)
        if funds < price:
            con.rollback()
            flash("Insufficient funds", "error")
            return redirect(url_for("machine_detail", machine_code=machine_code))

        changed = con.execute(
            f"UPDATE users SET {source}=COALESCE({source},0)-? "
            f"WHERE id=? AND COALESCE({source},0)>=?",
            (price, user["id"], price)
        ).rowcount
        if changed != 1:
            con.rollback()
            flash("Insufficient funds", "error")
            return redirect(url_for("machine_detail", machine_code=machine_code))
        con.execute("""
            INSERT INTO machine_purchases
                (user_id, machine_code, status, purchased_at, price, payment_source)
            VALUES (?, ?, 'successful', datetime('now'), ?, ?)
        """, (user["id"], machine_code, price, source))
        con.commit()
        flash("Purchase successful", "success")
    except Exception:
        con.rollback()
        app.logger.exception("Machine purchase failed")
        flash("Purchase could not be completed. Please try again.", "error")
    finally:
        con.close()

    return redirect(url_for("machine_detail", machine_code=machine_code))


@app.route("/api/exchange-rate")
def exchange_rate():
    import json
    from urllib.request import Request, urlopen
    from datetime import datetime, timezone

    url = "https://open.er-api.com/v6/latest/USD"
    try:
        req = Request(url, headers={"User-Agent": "SpaceXPlatform/1.0"})
        with urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        rate = data.get("rates", {}).get("UGX")
        if data.get("result") != "success" or not rate:
            raise ValueError("Exchange rate unavailable")
        return {
            "base": "USD",
            "target": "UGX",
            "rate": rate,
            "message": f"Live exchange rate: $1 USD = UGX {rate:,.2f}",
            "updated_at": data.get("time_last_update_utc") or datetime.now(timezone.utc).isoformat()
        }
    except Exception:
        app.logger.exception("Exchange-rate lookup failed")
        return {
            "base": "USD",
            "target": "UGX",
            "rate": None,
            "message": "USD/UGX exchange rate temporarily unavailable",
            "updated_at": None
        }, 503



@app.route("/deposit", methods=["GET", "POST"])
def deposit():
    import json, time
    from urllib.request import Request, urlopen
    if "uid" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        network = request.form.get("network", "").upper()
        phone = request.form.get("sender_phone", "").strip()
        try:
            amount = float(request.form.get("amount_ugx", ""))
        except (ValueError, TypeError):
            amount = 0
        if network not in ("MTN", "AIRTEL"):
            flash("Choose MTN or Airtel.", "error")
            return redirect(url_for("deposit"))
        if not phone or len(phone) < 9 or len(phone) > 16 or not 1000 <= amount <= 10000000:
            flash("Check the phone number and amount.", "error")
            return redirect(url_for("deposit"))
        try:
            req = Request("https://open.er-api.com/v6/latest/USD",
                          headers={"User-Agent": "SpaceXPlatform/1.0"})
            with urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
            rate = float(data["rates"]["UGX"])
            if data.get("result") != "success" or rate <= 0:
                raise ValueError("Rate unavailable")
        except Exception:
            flash("Exchange rate unavailable. Try again.", "error")
            return redirect(url_for("deposit"))
        with db() as c:
            cur = c.execute("""
                INSERT INTO deposit_requests
                (user_id,network,sender_phone,requested_ugx,exchange_rate,
                 requested_usd,status,created_at)
                VALUES (?,?,?,?,?,?,'pending',?)
            """, (session["uid"],network,phone,amount,rate,round(amount/rate,2),time.time()))
            did = cur.lastrowid
        return redirect(url_for("deposit_instructions", deposit_id=did))
    return render_template("deposit.html")


@app.route("/deposit/<int:deposit_id>", methods=["GET", "POST"])
def deposit_instructions(deposit_id):
    import time
    if "uid" not in session:
        return redirect(url_for("login"))
    with db() as c:
        row = c.execute(
            "SELECT * FROM deposit_requests WHERE id=? AND user_id=?",
            (deposit_id,session["uid"])).fetchone()
    if not row:
        flash("Deposit request not found.", "error")
        return redirect(url_for("deposit"))
    now = time.time()
    can_submit = row["created_at"]+60 <= now <= row["created_at"]+1800
    if request.method == "POST":
        if row["status"] != "pending" or row["transaction_id"]:
            flash("This request has already been submitted.", "error")
            return redirect(url_for("deposit_instructions",deposit_id=deposit_id))
        if not can_submit:
            flash("Submission window is closed.", "error")
            return redirect(url_for("deposit_instructions",deposit_id=deposit_id))
        tx = request.form.get("transaction_id","").strip()
        phone = request.form.get("paid_phone","").strip()
        try:
            paid = float(request.form.get("paid_ugx",""))
        except (ValueError,TypeError):
            paid = 0
        if not tx or len(tx)>100 or not phone or len(phone)<9 or len(phone)>16 or not 1000<=paid<=10000000:
            flash("Check the transaction ID, amount and paying number.", "error")
            return redirect(url_for("deposit_instructions",deposit_id=deposit_id))
        try:
            with db() as c:
                result = c.execute("""
                    UPDATE deposit_requests SET transaction_id=?,paid_ugx=?,
                    paid_phone=?,submitted_at=?
                    WHERE id=? AND user_id=? AND transaction_id IS NULL
                    AND status='pending'
                """,(tx,paid,phone,now,deposit_id,session["uid"]))
                if result.rowcount != 1:
                    raise ValueError("Request already submitted")
        except sqlite3.IntegrityError:
            flash("That transaction ID has already been used.", "error")
            return redirect(url_for("deposit_instructions",deposit_id=deposit_id))
        except ValueError:
            flash("This request has already been submitted.", "error")
            return redirect(url_for("deposit_instructions",deposit_id=deposit_id))
        flash("Payment details submitted and pending verification.", "success")
        return redirect(url_for("transactions"))
    return render_template("deposit_instructions.html",
        deposit=dict(row), can_submit=can_submit)




@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    token = os.environ.get("ADMIN_TOKEN", "")
    if not token:
        return "ADMIN_TOKEN is not configured.", 503
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("token",""), token):
            session["space_x_admin"] = True
            return redirect(url_for("admin_deposits"))
        flash("Invalid admin token.", "error")
    return render_template_string("""
    <h2>Admin login</h2>
    <form method="post">
    <input type="password" name="token" required>
    <button type="submit">Login</button></form>
    """)


@app.route("/admin/deposits", methods=["GET", "POST"])
def admin_deposits():
    import time
    if not session.get("space_x_admin"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        try:
            did = int(request.form.get("deposit_id", ""))
        except ValueError:
            flash("Invalid deposit ID.", "error")
            return redirect(url_for("admin_deposits"))

        action = request.form.get("action")
        with db() as c:
            d = c.execute("""SELECT * FROM deposit_requests
                WHERE id=? AND status='pending'
                AND transaction_id IS NOT NULL AND paid_ugx IS NOT NULL""",
                (did,)).fetchone()
            if not d:
                flash("Deposit missing or already reviewed.", "error")
                return redirect(url_for("admin_deposits"))

            if action == "reject":
                c.execute("""UPDATE deposit_requests
                    SET status='rejected', reviewed_at=?
                    WHERE id=? AND status='pending'""", (time.time(), did))
                flash("Deposit rejected.", "success")
                return redirect(url_for("admin_deposits"))

            if action != "approve" or d["paid_ugx"] <= 0 or d["exchange_rate"] <= 0:
                flash("Invalid action or amount.", "error")
                return redirect(url_for("admin_deposits"))

            changed = c.execute("""UPDATE deposit_requests
                SET status='approved', reviewed_at=?
                WHERE id=? AND status='pending'""",
                (time.time(), did)).rowcount
            if changed != 1:
                flash("Deposit already reviewed.", "error")
                return redirect(url_for("admin_deposits"))

            usd = round(d["paid_ugx"] / d["exchange_rate"], 3)
            row = c.execute("SELECT referred_by FROM users WHERE id=?",
                            (d["user_id"],)).fetchone()
            uid = row["referred_by"] if row else None

            for level, rate in ((1, .10), (2, .05), (3, .01)):
                if not uid:
                    break
                amount = round(usd * rate, 3)
                if amount > 0:
                    ins = c.execute("""INSERT OR IGNORE INTO referral_commissions
                        (deposit_id, beneficiary_user_id, referral_level,
                         amount_usd, created_at) VALUES (?, ?, ?, ?, ?)""",
                        (did, uid, level, amount, time.time()))
                    if ins.rowcount == 1:
                        c.execute("""UPDATE users SET balance=
                            ROUND(COALESCE(balance,0)+?,3) WHERE id=?""",
                            (amount, uid))
                parent = c.execute("SELECT referred_by FROM users WHERE id=?",
                                    (uid,)).fetchone()
                uid = parent["referred_by"] if parent else None

        flash("Deposit approved; eligible commissions credited automatically.", "success")
        return redirect(url_for("admin_deposits"))

    with db() as c:
        rows = c.execute("""SELECT d.*, u.phone AS user_phone
            FROM deposit_requests d JOIN users u ON u.id=d.user_id
            WHERE d.status='pending' AND d.transaction_id IS NOT NULL
            ORDER BY d.created_at""").fetchall()

    return render_template_string("""
    <h2>Deposit approvals</h2>
    <p>Verify each transaction with the mobile-money provider first.</p>
    {% for d in rows %}
      <hr><p>#{{ d.id }} | {{ d.user_phone }} | {{ d.network }}</p>
      <p>Transaction: {{ d.transaction_id }} | UGX {{ d.paid_ugx }}</p>
      <form method="post">
        <input type="hidden" name="deposit_id" value="{{ d.id }}">
        <button name="action" value="approve">Approve and credit commissions</button>
        <button name="action" value="reject">Reject</button>
      </form>
    {% else %}<p>No submitted deposits awaiting review.</p>{% endfor %}
    """, rows=rows)


@app.route("/admin/logout")
def admin_logout():
    session.pop("space_x_admin", None)
    return redirect(url_for("admin_login"))


@app.route("/invite")
def invite():
    if "uid" not in session:
        return redirect(url_for("login"))

    with db() as c:
        user = c.execute(
            "SELECT id, referral_code FROM users WHERE id=?",
            (session["uid"],)
        ).fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        code = user["referral_code"]
        if not code:
            code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
            c.execute(
                "UPDATE users SET referral_code=? WHERE id=?",
                (code, session["uid"])
            )

        count = c.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=?",
            (session["uid"],)
        ).fetchone()[0]

    referral_link = request.host_url.rstrip("/") + url_for("register") + "?ref=" + code
    return render_template(
        "invite.html",
        referral_code=code,
        referral_link=referral_link,
        team_count=count
    )


@app.route("/team")
def my_team():
    if "uid" not in session:
        return redirect(url_for("login"))

    with db() as c:
        members = c.execute(
            "SELECT phone, created_at FROM users WHERE referred_by=? ORDER BY id DESC",
            (session["uid"],)
        ).fetchall()

    return render_template("team.html", members=members)

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
