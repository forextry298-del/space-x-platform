from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, secrets, string
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-deployment")
DB = os.environ.get("DATABASE_PATH", "space_x.db")
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, phone TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL)")
@app.route("/")
def index(): return redirect(url_for("register"))
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        phone=request.form.get("phone","").strip()
        pw=request.form.get("password","")
        confirm=request.form.get("confirm","")
        code=request.form.get("captcha_input","").strip()
        real=request.form.get("real_captcha","").strip()
        if not phone or not pw: flash("Phone number and password are required.","error")
        elif pw != confirm: flash("Passwords do not match.","error")
        elif len(pw)<8: flash("Use at least 8 characters for your password.","error")
        elif not code or code != real: flash("Incorrect verification code.","error")
        else:
            try:
                with db() as c: c.execute("INSERT INTO users(phone,password_hash) VALUES(?,?)",(phone,generate_password_hash(pw)))
                flash("Account created. Please log in.","success"); return redirect(url_for("login"))
            except sqlite3.IntegrityError: flash("Phone number already registered.","error")
    real_captcha = ''.join(secrets.choice(string.digits) for _ in range(4))
    return render_template("register.html", real_captcha=real_captcha,
                           invite=request.args.get("ref", ""))
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
@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
