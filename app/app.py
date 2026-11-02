"""Blitz Prints Live: the Lab 5 print shop as a dynamic site.

Accounts, the product catalogue and orders live in PostgreSQL. Passwords are hashed
(scrypt), sessions are signed cookies, and every form carries a CSRF token.
"""
import json
import os
import secrets
import socket
import time
from functools import wraps
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

HERE = Path(__file__).resolve().parent
COLORS = {"cyan": "#3fa9e6", "magenta": "#e0559f", "yellow": "#f5c518", "white": "#f2f2ee", "black": "#1a1a1d",
          "green": "#3f9c4a", "red": "#c8202f", "grey": "#8f949a", "teal": "#1f9e98", "orange": "#f07a26",
          "lilac": "#9b84d6", "sand": "#d9c7a3"}
CATS = {"figurines": "Figurines", "botanicals": "Botanicals", "originals": "Studio originals", "workshop": "Workshop"}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
DSN = os.environ["DATABASE_URL"]
HOST = socket.gethostname()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            SERIAL PRIMARY KEY,
  username      VARCHAR(32) UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS products (
  slug      VARCHAR(64) PRIMARY KEY,
  name      TEXT NOT NULL,
  category  VARCHAR(32) NOT NULL,
  price_aed INTEGER NOT NULL,
  colors    TEXT[] NOT NULL,
  status    VARCHAR(16) NOT NULL,
  height_mm INTEGER,
  print_min INTEGER,
  grams     NUMERIC(6,1),
  featured  INTEGER,
  blurb     TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id         SERIAL PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product    VARCHAR(64) NOT NULL REFERENCES products(slug),
  qty        INTEGER NOT NULL CHECK (qty BETWEEN 1 AND 9),
  color      VARCHAR(16) NOT NULL,
  note       VARCHAR(200) NOT NULL DEFAULT '',
  total_aed  INTEGER NOT NULL,
  served_by  VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def connect():
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=True)


def init_db():
    """Create tables and load the catalogue. Retries while the database container starts."""
    for attempt in range(30):
        try:
            with connect() as c:
                c.execute(SCHEMA)
                for p in json.loads((HERE / "catalog.json").read_text(encoding="utf8")):
                    c.execute(
                        """INSERT INTO products VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (slug) DO NOTHING""",
                        (p["slug"], p["name"], p["cat"], p["price"], p["colors"], p["status"], p["height"],
                         p["print_min"], p["grams"], p["featured"], p["blurb"]))
            return
        except psycopg.OperationalError:
            time.sleep(2)
    raise SystemExit("database not reachable")


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c:
        c.close()


@app.before_request
def load_user():
    g.user = None
    if "uid" in session:
        g.user = db().execute("SELECT id, username FROM users WHERE id=%s", (session["uid"],)).fetchone()
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "-")):
            abort(400, "Form expired, go back and try again.")


@app.context_processor
def globals_():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return {"COLORS": COLORS, "CATS": CATS, "HOST": HOST, "csrf": session["csrf"]}


@app.template_filter("ptime")
def ptime(m):
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            flash("Log in first.")
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


@app.get("/")
def home():
    featured = db().execute("SELECT * FROM products WHERE featured IS NOT NULL ORDER BY featured LIMIT 4").fetchall()
    stats = db().execute("""SELECT (SELECT count(*) FROM products) AS products,
                                   (SELECT count(*) FROM users) AS users,
                                   (SELECT count(*) FROM orders) AS orders""").fetchone()
    return render_template("home.html", featured=featured, stats=stats)


@app.get("/shop")
def shop():
    cat = request.args.get("cat")
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM products WHERE TRUE", []
    if cat in CATS:
        sql += " AND category=%s"; args.append(cat)
    if q:
        sql += " AND (name ILIKE %s OR blurb ILIKE %s)"; args += [f"%{q}%"] * 2
    rows = db().execute(sql + " ORDER BY featured NULLS LAST, name", args).fetchall()
    return render_template("shop.html", products=rows, cat=cat, q=q)


@app.route("/shop/<slug>", methods=["GET", "POST"])
def item(slug):
    p = db().execute("SELECT * FROM products WHERE slug=%s", (slug,)).fetchone() or abort(404)
    if request.method == "POST":
        if not g.user:
            return redirect(url_for("login", next=request.path))
        qty = max(1, min(9, int(request.form.get("qty", 1) or 1)))
        color = request.form.get("color")
        if color not in p["colors"]:
            color = p["colors"][0]
        note = request.form.get("note", "")[:200]
        o = db().execute(
            """INSERT INTO orders (user_id, product, qty, color, note, total_aed, served_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (g.user["id"], slug, qty, color, note, qty * p["price_aed"], HOST)).fetchone()
        flash(f"Order #{o['id']} saved: {qty} × {p['name']} in {color}.")
        return redirect(url_for("orders"))
    return render_template("item.html", p=p)


@app.get("/orders")
@login_required
def orders():
    rows = db().execute(
        """SELECT o.*, p.name, p.slug FROM orders o JOIN products p ON p.slug=o.product
           WHERE o.user_id=%s ORDER BY o.id DESC""", (g.user["id"],)).fetchall()
    return render_template("orders.html", orders=rows, total=sum(r["total_aed"] for r in rows))


@app.post("/orders/<int:oid>/cancel")
@login_required
def cancel(oid):
    db().execute("DELETE FROM orders WHERE id=%s AND user_id=%s", (oid, g.user["id"]))
    flash(f"Order #{oid} cancelled.")
    return redirect(url_for("orders"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        if not (3 <= len(u) <= 32 and u.replace("_", "").isalnum()):
            flash("Username: 3-32 letters, digits or _.")
        elif len(pw) < 8:
            flash("Password: at least 8 characters.")
        else:
            try:
                r = db().execute("INSERT INTO users (username, password_hash) VALUES (%s,%s) RETURNING id",
                                 (u, generate_password_hash(pw))).fetchone()
            except psycopg.errors.UniqueViolation:
                flash("That username is taken.")
            else:
                session.clear(); session["uid"] = r["id"]
                flash(f"Welcome, {u}. Your account is ready.")
                return redirect(url_for("shop"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip().lower()
        r = db().execute("SELECT * FROM users WHERE username=%s", (u,)).fetchone()
        if r and check_password_hash(r["password_hash"], request.form.get("password", "")):
            session.clear(); session["uid"] = r["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("orders"))
        flash("Wrong username or password.")
    return render_template("auth.html", mode="login")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/health")
def health():
    db().execute("SELECT 1")
    return {"ok": True, "host": HOST}


init_db()
