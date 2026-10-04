"""
Solution 4 Finance — website backend (Flask)

Run locally:
    pip install -r requirements.txt
    python app.py
    -> open http://127.0.0.1:5000

Production (example):
    gunicorn -w 2 -b 0.0.0.0:8000 app:app

Optional environment variables:
    S4F_DB        Path to the SQLite file for loan enquiries (default: ./leads.db)
    S4F_ADMIN_KEY Secret key to view enquiries at /admin/leads?key=<S4F_ADMIN_KEY>
    PORT          Port for `python app.py` (default: 5000)
"""

import os
import re
import sqlite3
from datetime import datetime

from flask import Flask, abort, g, jsonify, render_template, request

app = Flask(__name__)

DB_PATH = os.environ.get("S4F_DB", os.path.join(app.root_path, "leads.db"))
ADMIN_KEY = os.environ.get("S4F_ADMIN_KEY", "")

# Indicative starting rates shown on the site (keep in sync with index.html)
LOAN_PRODUCTS = {
    "home": {"name": "Home Loan", "rate_from": 8.40},
    "construction": {"name": "Construction Loan", "rate_from": 8.50},
    "car": {"name": "Car / Auto Loan", "rate_from": 8.50},
    "personal": {"name": "Personal Loan", "rate_from": 10.50},
    "business": {"name": "Business Loan", "rate_from": None},
    "insurance": {"name": "Insurance", "rate_from": None},
}

PHONE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                name        TEXT NOT NULL,
                phone       TEXT NOT NULL,
                email       TEXT,
                city        TEXT,
                loan_type   TEXT NOT NULL,
                amount      INTEGER,
                message     TEXT,
                ip          TEXT
            )
            """
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def calculate_emi(principal: float, annual_rate: float, years: float) -> dict:
    """Standard reducing-balance EMI."""
    months = int(round(years * 12))
    if principal <= 0 or months <= 0:
        raise ValueError("Amount and tenure must be greater than zero.")
    r = annual_rate / 12 / 100
    if r == 0:
        emi = principal / months
    else:
        factor = (1 + r) ** months
        emi = principal * r * factor / (factor - 1)
    total = emi * months
    return {
        "emi": round(emi, 2),
        "total_interest": round(total - principal, 2),
        "total_payable": round(total, 2),
        "months": months,
    }


def clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/rates")
def api_rates():
    return jsonify(LOAN_PRODUCTS)


@app.route("/api/emi", methods=["GET", "POST"])
def api_emi():
    data = request.get_json(silent=True) or request.values
    try:
        result = calculate_emi(
            float(data.get("amount", 0)),
            float(data.get("rate", 0)),
            float(data.get("years", 0)),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/apply")
def api_apply():
    data = request.get_json(silent=True) or request.form

    # Honeypot field: real visitors never fill it in
    if (data.get("website") or "").strip():
        return jsonify({"ok": True})

    name = (data.get("name") or "").strip()
    phone = clean_phone(data.get("phone", ""))
    email = (data.get("email") or "").strip()
    city = (data.get("city") or "").strip()
    loan_type = (data.get("loan_type") or "").strip().lower()
    message = (data.get("message") or "").strip()[:1000]

    errors = {}
    if len(name) < 2:
        errors["name"] = "Enter your full name."
    if not PHONE_RE.match(phone):
        errors["phone"] = "Enter a 10-digit Indian mobile number."
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address, or leave it blank."
    if loan_type not in LOAN_PRODUCTS:
        errors["loan_type"] = "Choose the type of loan you need."

    amount = None
    raw_amount = re.sub(r"[^\d]", "", str(data.get("amount") or ""))
    if raw_amount:
        amount = int(raw_amount)
        if amount < 10000:
            errors["amount"] = "Loan amount should be at least ₹10,000."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 422

    db = get_db()
    db.execute(
        """INSERT INTO leads
           (created_at, name, phone, email, city, loan_type, amount, message, ip)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            name, phone, email or None, city or None,
            loan_type, amount, message or None,
            request.headers.get("X-Forwarded-For", request.remote_addr),
        ),
    )
    db.commit()

    return jsonify({
        "ok": True,
        "message": f"Thanks {name.split()[0]}, we've received your "
                   f"{LOAN_PRODUCTS[loan_type]['name'].lower()} enquiry. "
                   f"An advisor will call you on {phone[:5]} {phone[5:]} shortly.",
    })


@app.get("/admin/leads")
def admin_leads():
    if not ADMIN_KEY or request.args.get("key") != ADMIN_KEY:
        abort(404)
    rows = get_db().execute("SELECT * FROM leads ORDER BY id DESC LIMIT 500").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/health")
def health():
    return {"status": "ok"}


init_db()


# --------------------------------------------------------------------------
# Streamlit Community Cloud support
# --------------------------------------------------------------------------
# Streamlit Cloud runs this file with `streamlit run app.py`. It cannot host a
# Flask server, so there we render the same page inside Streamlit instead.
# The enquiry form then sends the visitor's details via WhatsApp, because
# there is no Flask /api/apply endpoint to post to.
def running_in_streamlit() -> bool:
    try:
        from streamlit.runtime import exists
        return exists()
    except Exception:
        return False


def run_streamlit_page():
    import streamlit as st
    import streamlit.components.v1 as components

    st.set_page_config(
        page_title="Solution 4 Finance | Home, Personal, Business & Car Loans",
        page_icon="🏦",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Hide Streamlit's own chrome and let the page fill the browser window
    st.markdown(
        """
        <style>
          [data-testid="stHeader"], [data-testid="stToolbar"],
          [data-testid="stDecoration"], #MainMenu, footer { display: none !important; }
          .block-container, [data-testid="stMainBlockContainer"] {
            padding: 0 !important; max-width: 100% !important;
          }
          [data-testid="stVerticalBlock"] { gap: 0 !important; }
          iframe { display: block; width: 100% !important; height: 100vh !important; border: 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    template_path = os.path.join(app.root_path, "templates", "index.html")
    with open(template_path, encoding="utf-8") as fh:
        page = fh.read()
    page = page.replace(
        "<!--S4F_MODE-->", '<script>window.S4F_MODE = "streamlit";</script>', 1
    )

    components.html(page, height=900, scrolling=True)


if running_in_streamlit():
    run_streamlit_page()
elif __name__ == "__main__":
    # Local development: `python app.py`. Set FLASK_DEBUG=1 for auto-reload.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
