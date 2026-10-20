"""AquaFlow backend: website, accounts, quotes and orders."""
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SAMESITE="Lax")

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "aquaflow.db"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+\-\s()]{7,20}$")
PIN_RE = re.compile(r"^\d{6}$")

# Prices live on the server so the browser can't change them.
PRODUCTS = {
    "b100":  {"name": "100 ml Bottles",  "price": 600,  "min": 25},
    "b250":  {"name": "250 ml Bottles",  "price": 900,  "min": 25},
    "b750":  {"name": "750 ml Bottles",  "price": 1500, "min": 25},
    "b1000": {"name": "1000 ml Bottles", "price": 1800, "min": 25},
    "jar20": {"name": "20 Litre Jar",    "price": 200,  "min": 1},
}
MAX_QTY = 1000

# TEST ACCOUNTS: change these passwords before the site goes live.
SEED_PASSWORD = "1234"
SEED_ACCOUNTS = (
    [(f"staff{i}", "staff", f"Staff {i}") for i in range(1, 5)] +
    [(f"driver{i}", "driver", f"Driver {i}") for i in range(1, 9)]
)


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                company TEXT,
                customer_type TEXT,
                message TEXT,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                addr1 TEXT NOT NULL,
                addr2 TEXT,
                city TEXT NOT NULL,
                pin TEXT NOT NULL,
                items TEXT NOT NULL,
                subtotal INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                created_at TEXT NOT NULL
            )
        """)


def seed_accounts():
    """Create the staff and driver test accounts if they don't exist yet."""
    with sqlite3.connect(DATABASE_PATH) as db:
        for username, role, name in SEED_ACCOUNTS:
            exists = db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO users (username, password_hash, role, name, created_at) VALUES (?, ?, ?, ?, ?)",
                    (username, generate_password_hash(SEED_PASSWORD), role, name, now_iso()),
                )


# ---------- helpers ----------
def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not ADMIN_TOKEN or auth != f"Bearer {ADMIN_TOKEN}":
            return jsonify(error="Unauthorized"), 401
        return view(*args, **kwargs)
    return wrapper


def clean(value, max_len):
    return str(value or "").strip()[:max_len]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def validate_quote(data):
    errors = {}
    cleaned = {
        "name": clean(data.get("name"), 100),
        "email": clean(data.get("email"), 150),
        "phone": clean(data.get("phone"), 20),
        "company": clean(data.get("company"), 120),
        "customer_type": clean(data.get("customer_type"), 40),
        "message": clean(data.get("message"), 1000),
    }
    if not cleaned["name"]:
        errors["name"] = "Name is required."
    if not EMAIL_RE.match(cleaned["email"]):
        errors["email"] = "A valid email is required."
    if cleaned["phone"] and not PHONE_RE.match(cleaned["phone"]):
        errors["phone"] = "Phone number looks invalid."
    return cleaned, errors


def validate_order(data):
    errors = {}
    cleaned = {
        "name": clean(data.get("name"), 100),
        "phone": clean(data.get("phone"), 20),
        "addr1": clean(data.get("addr1"), 200),
        "addr2": clean(data.get("addr2"), 200),
        "city": clean(data.get("city"), 100),
        "pin": clean(data.get("pin"), 6),
    }
    if not cleaned["name"]:
        errors["name"] = "Name is required."
    if not PHONE_RE.match(cleaned["phone"]):
        errors["phone"] = "A valid phone number is required."
    if not cleaned["addr1"]:
        errors["addr1"] = "Address is required."
    if not cleaned["city"]:
        errors["city"] = "City is required."
    if not PIN_RE.match(cleaned["pin"]):
        errors["pin"] = "PIN code must be 6 digits."

    items, subtotal = [], 0
    for item in data.get("items") or []:
        pid = str(item.get("id", ""))
        try:
            qty = int(item.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if pid not in PRODUCTS:
            errors["items"] = "Unknown product selected."
            break
        if qty == 0:
            continue
        p = PRODUCTS[pid]
        if qty < p["min"] or qty > MAX_QTY:
            errors["items"] = f"{p['name']}: order between {p['min']} and {MAX_QTY}."
            break
        items.append({"id": pid, "name": p["name"], "qty": qty, "price": p["price"]})
        subtotal += qty * p["price"]
    if not items and "items" not in errors:
        errors["items"] = "Please choose at least one product."

    cleaned["items"] = items
    cleaned["subtotal"] = subtotal
    return cleaned, errors


# ---------- security ----------
@app.before_request
def check_csrf_token():
    """Every POST to /api/ must send the token that /api/csrf gave this browser."""
    if request.method == "POST" and request.path.startswith("/api/") and request.path != "/api/csrf":
        token = request.headers.get("X-CSRF-Token", "")
        if not token or token != session.get("csrf"):
            return jsonify(error="Your session expired. Please refresh the page and try again."), 403


@app.errorhandler(404)
def not_found(err):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return err


# ---------- routes ----------
@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.get("/api/csrf")
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return jsonify(token=session["csrf"])


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = clean(data.get("username"), 150).lower()
    password = str(data.get("password") or "")

    user = get_db().execute(
        "SELECT * FROM users WHERE username = ? OR email = ?", (username, username)
    ).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify(error="Wrong user ID or password."), 401

    session["user_id"] = user["id"]
    session["role"] = user["role"]
    return jsonify(ok=True, role=user["role"], name=user["name"], username=user["username"])


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    name = clean(data.get("name"), 100)
    phone = clean(data.get("phone"), 20)
    email = clean(data.get("email"), 150).lower()
    password = str(data.get("password") or "")

    errors = {}
    if not name:
        errors["name"] = "Name is required."
    if not PHONE_RE.match(phone):
        errors["phone"] = "A valid phone number is required."
    if not EMAIL_RE.match(email):
        errors["email"] = "A valid email is required."
    if len(password) < 6:
        errors["password"] = "Password must be at least 6 characters."
    if errors:
        return jsonify(error=" ".join(errors.values()), fields=errors), 400

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username = ? OR email = ?", (email, email)).fetchone():
        return jsonify(error="An account with this email already exists."), 409

    cur = db.execute(
        """INSERT INTO users (username, password_hash, role, name, phone, email, created_at)
           VALUES (?, ?, 'client', ?, ?, ?, ?)""",
        (email, generate_password_hash(password), name, phone, email, now_iso()),
    )
    db.commit()
    session["user_id"] = cur.lastrowid
    session["role"] = "client"
    return jsonify(ok=True, role="client", name=name), 201


@app.post("/api/quote")
def create_quote():
    cleaned, errors = validate_quote(request.get_json(silent=True) or {})
    if errors:
        return jsonify(error="Validation failed", fields=errors), 400

    db = get_db()
    cur = db.execute(
        """INSERT INTO quotes (name, email, phone, company, customer_type, message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (cleaned["name"], cleaned["email"], cleaned["phone"], cleaned["company"],
         cleaned["customer_type"], cleaned["message"], now_iso()),
    )
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid,
                   message="Thanks! We've received your request and will be in touch shortly."), 201


@app.post("/api/orders")
def create_order():
    cleaned, errors = validate_order(request.get_json(silent=True) or {})
    if errors:
        return jsonify(error="Validation failed", fields=errors), 400

    db = get_db()
    cur = db.execute(
        """INSERT INTO orders (name, phone, addr1, addr2, city, pin, items, subtotal, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cleaned["name"], cleaned["phone"], cleaned["addr1"], cleaned["addr2"],
         cleaned["city"], cleaned["pin"], json.dumps(cleaned["items"]),
         cleaned["subtotal"], now_iso()),
    )
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid, subtotal=cleaned["subtotal"],
                   message="Order placed! We'll confirm your delivery slot shortly."), 201


@app.get("/api/quotes")
@require_admin
def list_quotes():
    rows = get_db().execute("SELECT * FROM quotes ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify(quotes=[dict(r) for r in rows])


@app.get("/api/orders")
@require_admin
def list_orders():
    rows = get_db().execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200").fetchall()
    orders = []
    for r in rows:
        o = dict(r)
        o["items"] = json.loads(o["items"])
        orders.append(o)
    return jsonify(orders=orders)


# Create tables and test accounts when the server starts.
init_db()
seed_accounts()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 5000)))
