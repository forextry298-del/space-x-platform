from pathlib import Path
from flask import request, session, redirect, url_for, render_template_string
from app import app, db
import os

@app.route("/admin")
def simple_admin():
    if "uid" not in session:
        return redirect(url_for("login"))
    phone = str(session.get("phone", ""))
    admin = os.getenv("SPACE_X_ADMIN_PHONE", "0758878297")
    if phone != admin:
        return "Access denied", 403
    return render_template_string(Path("templates/admin_home.html").read_text())

@app.route("/admin/deposits", methods=["GET", "POST"])
def admin_deposits():
    if "uid" not in session:
        return redirect(url_for("login"))
    if str(session.get("phone", "")) != os.getenv("SPACE_X_ADMIN_PHONE", "0758878297"):
        return "Access denied", 403

    with db() as c:
        if request.method == "POST":
            did = request.form.get("id", type=int)
            action = request.form.get("action")
            if did and action in ("approve", "reject"):
                c.execute("BEGIN IMMEDIATE")
                dep = c.execute(
                    "SELECT * FROM deposit_requests WHERE id=? AND status='pending'",
                    (did,)
                ).fetchone()
                if dep and action == "reject":
                    c.execute(
                        "UPDATE deposit_requests SET status='rejected', reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                        (did,)
                    )
                elif dep and action == "approve":
                    approved = c.execute(
                        "UPDATE deposit_requests SET status='approved', reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                        (did,)
                    )
                    if approved.rowcount == 1:
                        credited = float(dep["paid_ugx"] or dep["requested_ugx"] or 0) / max(float(dep["exchange_rate"] or 1), 0.000001)
                        c.execute(
                            "UPDATE users SET balance=COALESCE(balance,0)+? WHERE id=?",
                            (round(credited, 2), dep["user_id"])
                        )
                    previous = c.execute(
                        "SELECT 1 FROM deposit_requests WHERE user_id=? AND status='approved' AND id<>? LIMIT 1",
                        (dep["user_id"], did)
                    ).fetchone()
                    invitee = c.execute(
                        "SELECT referred_by FROM users WHERE id=?",
                        (dep["user_id"],)
                    ).fetchone()
                    if not previous and invitee and invitee["referred_by"]:
                        inviter = c.execute(
                            "SELECT id FROM users WHERE referral_code=?",
                            (invitee["referred_by"],)
                        ).fetchone()
                        reward = float(dep["paid_ugx"] or 0) / max(float(dep["exchange_rate"] or 1), 0.000001) * 0.10
                        if inviter and reward > 0:
                            cur = c.execute(
                                "INSERT OR IGNORE INTO referral_commissions (deposit_id, beneficiary_user_id, referral_level, amount_usd) VALUES (?, ?, 1, ?)",
                                (did, inviter["id"], reward)
                            )
                            if cur.rowcount == 1:
                                c.execute(
                                    "UPDATE users SET balance=COALESCE(balance,0)+? WHERE id=?",
                                    (reward, inviter["id"])
                                )
                c.commit()

        rows = c.execute(
            "SELECT * FROM deposit_requests WHERE status='pending' ORDER BY id DESC"
        ).fetchall()

    return render_template_string("""
    <h2>Pending deposits</h2>
    {% for d in rows %}
      <p>#{{ d.id }} | User {{ d.user_id }} | Amount: {{ d.requested_ugx }} UGX<br>Deposit phone: {{ d.sender_phone or "Not provided" }}<br>Transaction ID: {{ d.transaction_id or "Not provided" }}</p>
      <form method="post">
        <input type="hidden" name="id" value="{{ d.id }}">
        <button name="action" value="approve">Approve</button>
        <button name="action" value="reject">Reject</button>
      </form><hr>
    {% else %}
      <p>No pending deposits.</p>
    {% endfor %}
    <a href="/admin">Back to Admin</a>
    """, rows=rows)


@app.route("/admin/withdrawals", methods=["GET", "POST"])
def admin_withdrawals():
    if "uid" not in session:
        return redirect(url_for("login"))
    if str(session.get("phone", "")) != os.getenv("SPACE_X_ADMIN_PHONE", "0758878297"):
        return "Access denied", 403

    with db() as c:
        if request.method == "POST":
            wid = request.form.get("id", type=int)
            action = request.form.get("action")
            if wid and action in ("approve", "reject"):
                c.execute("BEGIN IMMEDIATE")
                w = c.execute(
                    "SELECT * FROM withdrawal_requests WHERE id=? AND status='pending'",
                    (wid,)
                ).fetchone()
                if w and action == "approve":
                    cur = c.execute(
                        "UPDATE withdrawal_requests SET status='approved' WHERE id=? AND status='pending'",
                        (wid,)
                    )
                    if cur.rowcount == 1:
                        c.execute(
                            "UPDATE users SET balance=COALESCE(balance,0)-? WHERE id=?",
                            (w["amount_usd"], w["user_id"])
                        )
                elif w and action == "reject":
                    c.execute(
                        "UPDATE withdrawal_requests SET status='rejected' WHERE id=? AND status='pending'",
                        (wid,)
                    )
                c.commit()

        rows = c.execute(
            "SELECT * FROM withdrawal_requests WHERE status='pending' ORDER BY id DESC"
        ).fetchall()

    return render_template_string("""
    <h2>Pending withdrawals</h2>
    {% for w in rows %}
      <p>#{{ w.id }} | User {{ w.user_id }} | ${{ w.amount_usd }} | {{ w.recipient_phone }}</p>
      <form method="post">
        <input type="hidden" name="id" value="{{ w.id }}">
        <button name="action" value="approve">Approve</button>
        <button name="action" value="reject">Reject</button>
      </form><hr>
    {% else %}
      <p>No pending withdrawals.</p>
    {% endfor %}
    <a href="/admin">Back to Admin</a>
    """, rows=rows)

with db() as c:
    c.execute("""CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

@app.route("/admin/announcement", methods=["GET", "POST"])
def admin_announcement():
    if "uid" not in session:
        return redirect(url_for("login"))
    if str(session.get("phone", "")) != os.getenv("SPACE_X_ADMIN_PHONE", "0758878297"):
        return "Access denied", 403

    with db() as c:
        if request.method == "POST":
            message = request.form.get("message", "").strip()
            if message:
                c.execute("UPDATE announcements SET active=0")
                c.execute(
                    "INSERT INTO announcements(message,active) VALUES(?,1)",
                    (message,)
                )
        current = c.execute(
            "SELECT message FROM announcements WHERE active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return render_template_string("""
    <h2>Publish announcement</h2>
    <form method="post">
      <textarea name="message" required placeholder="Write announcement"></textarea>
      <button type="submit">Publish</button>
    </form>
    {% if current %}<p>Current: {{ current.message }}</p>{% endif %}
    <a href="/admin">Back to Admin</a>
    """, current=current)

@app.context_processor
def space_x_announcement():
    if "uid" not in session:
        return {}
    try:
        with db() as c:
            row = c.execute(
                "SELECT message FROM announcements WHERE active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {"space_x_notice": row["message"] if row else ""}
    except Exception:
        return {"space_x_notice": ""}

with db() as c:
    c.execute("""
        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            network TEXT NOT NULL,
            recipient_phone TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            exchange_rate REAL NOT NULL,
            amount_ugx REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
