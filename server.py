import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING, DESCENDING
import bcrypt
from datetime import datetime, timedelta
from bson import ObjectId

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_folder = os.path.join(BASE_DIR, "static")
if not os.path.isdir(static_folder):
    static_folder = os.path.join(BASE_DIR, "public")
if not os.path.isdir(static_folder):
    static_folder = os.path.join(os.getcwd(), "static")

app = Flask(__name__, static_folder=static_folder)
CORS(app)  # Enable Cross-Origin Resource Sharing

# =========================================================
# DATABASE CONNECTION
# =========================================================
try:
    import certifi
    ca_file = certifi.where()
except Exception:
    ca_file = None

DEFAULT_ATLAS_URI = "mongodb+srv://admin:admin123@cluster0.ectwcvz.mongodb.net/jailDB?retryWrites=true&w=majority&appName=Cluster0"
mongo_uri = os.environ.get("MONGO_URI") or DEFAULT_ATLAS_URI

def create_mongo_client(uri):
    kwargs = {"serverSelectionTimeoutMS": 5000}
    if ca_file and ("mongodb+srv://" in uri or "ssl=true" in uri.lower() or "tls=true" in uri.lower()):
        kwargs["tlsCAFile"] = ca_file
    return MongoClient(uri, **kwargs)

try:
    client = create_mongo_client(mongo_uri)
    try:
        db = client.get_default_database()
    except Exception:
        db = client["jailDB"]
    if db is None:
        db = client["jailDB"]
except Exception as e:
    print(f"Warning: Initial database connection error: {e}")
    client = create_mongo_client(DEFAULT_ATLAS_URI)
    db = client["jailDB"]

users = db["users"]
prisoners = db["prisoners"]
staff = db["staff"]
visitors = db["visitors"]
cells = db["cells"]
incidents = db["incidents"]
audit_logs = db["audit_logs"]

# Index helpers
def ensure_indexes():
    index_configs = [
        (users, "username"),
        (prisoners, "prisoner_id"),
        (staff, "staff_id"),
        (visitors, "visit_id"),
        (cells, "cell_id"),
        (incidents, "incident_id"),
    ]
    for collection, field in index_configs:
        try:
            existing_indexes = list(collection.list_indexes())
            found = False
            for index in existing_indexes:
                keys = list(index.get("key", {}).keys())
                if keys == [field]:
                    found = True
                    break
            if not found:
                collection.create_index([(field, ASCENDING)], unique=True, name=f"{field}_unique")
        except Exception as e:
            print(f"Warning Index setup: {collection.name}.{field}: {e}")

try:
    ensure_indexes()
except Exception as e:
    print(f"Index setup warning: {e}")

# Helper to serialize MongoDB documents to JSON
def serialize_doc(doc):
    if doc is None:
        return None
    doc["_id"] = str(doc["_id"])
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.strftime("%Y-%m-%d %H:%M:%S")
    return doc

def serialize_list(docs):
    return [serialize_doc(doc) for doc in docs]

# Middleware helper for role authorization
def authorize(allowed_roles):
    role = request.headers.get("X-User-Role")
    if not role or role not in allowed_roles:
        return False
    return True

# Audit logging helper
def log_action(username, action, details=""):
    try:
        audit_logs.insert_one({
            "username": username,
            "action": action,
            "details": details,
            "timestamp": datetime.now()
        })
    except Exception as e:
        print(f"Failed to log audit action: {e}")

def get_user_from_headers():
    return request.headers.get("X-User-Name", "System")

# Recalculate cell occupancy from prisoners list
def sync_cell_occupancy():
    try:
        for cell in cells.find({}):
            cid = str(cell.get("cell_id", "")).strip()
            if not cid:
                continue
            occupied = prisoners.count_documents({"cell": cid})
            capacity = int(cell.get("capacity", 0) or 0)
            if occupied >= capacity and capacity > 0:
                status = "FULL"
            elif occupied > 0:
                status = "Occupied"
            else:
                status = "Available"
            cells.update_one(
                {"_id": cell["_id"]},
                {"$set": {
                    "occupied": occupied,
                    "available": max(capacity - occupied, 0),
                    "status": status,
                    "updated_at": datetime.now()
                }}
            )
    except Exception as e:
        print(f"Warning cell sync: {e}")

# Seed admin user
def seed_admin():
    if users.count_documents({}) == 0:
        hashed = bcrypt.hashpw("admin123".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        users.insert_one({
            "username": "admin",
            "password": hashed,
            "role": "Admin",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

try:
    seed_admin()
    sync_cell_occupancy()
except Exception as e:
    print(f"Database init warning: {e}")

# =========================================================
# STATIC FILE SERVING & PATH FIXING
# =========================================================
_INDEX_HTML_CACHE = None

def get_index_html():
    global _INDEX_HTML_CACHE
    if _INDEX_HTML_CACHE:
        return _INDEX_HTML_CACHE
    possible_paths = [
        os.path.join(BASE_DIR, "index.html"),
        os.path.join(os.getcwd(), "index.html"),
        os.path.join(os.path.dirname(BASE_DIR), "index.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"),
        "index.html",
        os.path.join(BASE_DIR, "public", "index.html"),
        os.path.join(BASE_DIR, "static", "index.html"),
        os.path.join(os.getcwd(), "public", "index.html"),
        os.path.join(os.getcwd(), "static", "index.html")
    ]
    for p in possible_paths:
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _INDEX_HTML_CACHE = f.read()
                    return _INDEX_HTML_CACHE
            except Exception:
                pass
    return None

def find_static_file(filename):
    for candidate_dir in [
        BASE_DIR,
        os.getcwd(),
        app.static_folder,
        os.path.join(BASE_DIR, "public"),
        os.path.join(BASE_DIR, "static"),
        os.path.join(os.getcwd(), "public"),
        os.path.join(os.getcwd(), "static")
    ]:
        if candidate_dir and os.path.exists(os.path.join(candidate_dir, filename)):
            return candidate_dir, filename
    return None, None

@app.route("/")
def serve_index():
    html = get_index_html()
    if html:
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    folder, f = find_static_file("index.html")
    if folder:
        return send_from_directory(folder, "index.html")
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:path>")
def serve_static(path):
    if path and not path.startswith("api/"):
        folder, f = find_static_file(path)
        if folder:
            return send_from_directory(folder, f)
    if not path.startswith("api/"):
        html = get_index_html()
        if html:
            return html, 200, {"Content-Type": "text/html; charset=utf-8"}
        folder, _ = find_static_file("index.html")
        if folder:
            return send_from_directory(folder, "index.html")
    return jsonify({"error": "Not Found"}), 404

class VercelPathFixer(object):
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        # Extract the real user-requested route from Vercel headers
        req_uri = (
            environ.get('REQUEST_URI') or 
            environ.get('RAW_URI') or 
            environ.get('HTTP_X_MATCHED_PATH') or 
            environ.get('HTTP_X_FORWARDED_URI') or 
            environ.get('PATH_INFO') or 
            ''
        )
        if req_uri:
            path_part = req_uri.split('?')[0].strip()
            # If Vercel passed /api/index.py or /api/index, strip that prefix to find the subpath
            for prefix in ['/api/index.py', '/api/index']:
                if path_part.startswith(prefix):
                    path_part = path_part[len(prefix):] or '/'
                    break
            if path_part:
                environ['PATH_INFO'] = path_part
                environ['SCRIPT_NAME'] = ''
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathFixer(app.wsgi_app)

# =========================================================
# AUTHENTICATION ENDPOINTS
# =========================================================

# ── Gmail SMTP Configuration ─────────────────────────────
# Fill in your Gmail address and App Password below.
# To generate an App Password:
#  Gmail → Settings → Security → 2-Step Verification → App Passwords
import smtplib, random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

GMAIL_SENDER   = os.environ.get("GMAIL_SENDER",  "hiteshtpc123@gmail.com")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "jqfx arim ihhb uszv")  # App Password

# In-memory OTP store: { username: { otp, expires_at } }
otp_store = {}

import string

def send_otp_email(to_email: str, username: str, otp: str) -> bool:
    """Send a styled OTP email via Gmail SMTP. Returns True on success."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🔐 Password Reset OTP — Jail Management System"
        msg["From"]    = f"JMS Security <{GMAIL_SENDER}>"
        msg["To"]      = to_email

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;background:#1a1a2e;border-radius:12px;padding:32px;color:#e2e8f0">
          <h2 style="color:#6366f1;margin-bottom:8px;">Password Reset Request</h2>
          <p style="color:#94a3b8;">Hello <strong style="color:#e2e8f0">{username}</strong>,</p>
          <p style="color:#94a3b8;">Your one-time password (OTP) for resetting your account password is:</p>
          <div style="background:#0f0f23;border:2px dashed #6366f1;border-radius:10px;padding:20px;text-align:center;margin:20px 0;">
            <span style="font-size:36px;font-weight:800;letter-spacing:12px;color:#10b981;font-family:monospace">{otp}</span>
          </div>
          <p style="color:#94a3b8;">⏱ This OTP is valid for <strong>10 minutes</strong>.</p>
          <p style="color:#94a3b8;">If you did not request this, please ignore this email.</p>
          <hr style="border-color:#2d2d44;margin:20px 0;">
          <p style="font-size:12px;color:#64748b;">Jail Management System — Security Team</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False

def send_temp_password_email(to_email: str, username: str, temp_pwd: str) -> bool:
    """Email a newly generated temporary password to the user. Returns True on success."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🔑 Your Temporary Password — Jail Management System"
        msg["From"]    = f"JMS Security <{GMAIL_SENDER}>"
        msg["To"]      = to_email

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;background:#1a1a2e;border-radius:12px;padding:32px;color:#e2e8f0">
          <h2 style="color:#6366f1;margin-bottom:8px;">Your Temporary Password</h2>
          <p style="color:#94a3b8;">Hello <strong style="color:#e2e8f0">{username}</strong>,</p>
          <p style="color:#94a3b8;">We received a forgot-password request for your account. Your new temporary password is:</p>
          <div style="background:#0f0f23;border:2px dashed #10b981;border-radius:10px;padding:20px;text-align:center;margin:20px 0;">
            <span style="font-size:28px;font-weight:800;letter-spacing:8px;color:#10b981;font-family:monospace">{temp_pwd}</span>
          </div>
          <p style="color:#94a3b8;">Please log in with this password and change it immediately from your profile settings.</p>
          <p style="color:#94a3b8;">If you did not request this, contact your Admin immediately.</p>
          <hr style="border-color:#2d2d44;margin:20px 0;">
          <p style="font-size:12px;color:#64748b;">Jail Management System — Security Team</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Temp-password email error: {e}")
        return False

@app.route("/api/auth/request-otp", methods=["POST"])
def request_otp():
    data  = request.json
    email = data.get("email", "").strip().lower()

    if not email:
        return jsonify({"error": "Email address is required"}), 400

    # Look up user by registered email
    user = users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not user:
        return jsonify({"error": "This email is not registered. Please sign up first."}), 404

    username = user.get("username", "")

    # Mask email: show first 2 chars + domain e.g. ab***@gmail.com
    at      = email.index("@")
    masked  = email[:2] + "***" + email[at:]

    # Generate 6-digit OTP (10 min expiry)
    otp = str(random.randint(100000, 999999))
    otp_store[email] = {          # key by email, not username
        "otp"       : otp,
        "username"  : username,
        "expires_at": datetime.now().timestamp() + 600
    }

    # Send real email — credentials must be configured
    sent = send_otp_email(email, username, otp)
    if not sent:
        otp_store.pop(email, None)
        return jsonify({"error": "Failed to send email. Server email is not configured yet — contact Admin."}), 500

    log_action(username, "REQUEST_OTP", f"OTP emailed to {masked}")
    return jsonify({"message": f"OTP sent to {masked}", "masked_email": masked}), 200

@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    data         = request.json
    email        = data.get("email", "").strip().lower()
    otp_input    = data.get("otp", "").strip()
    new_password = data.get("new_password", "")

    if not email or not otp_input or not new_password:
        return jsonify({"error": "All fields are required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    record = otp_store.get(email)
    if not record:
        return jsonify({"error": "No OTP found. Please request a new one."}), 400
    if datetime.now().timestamp() > record["expires_at"]:
        otp_store.pop(email, None)
        return jsonify({"error": "OTP has expired. Please request a new one."}), 400
    if record["otp"] != otp_input:
        return jsonify({"error": "Invalid OTP. Please try again."}), 400

    username = record["username"]
    hashed   = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    result   = users.update_one({"username": username}, {"$set": {"password": hashed}})
    if result.matched_count == 0:
        return jsonify({"error": "User not found"}), 404

    otp_store.pop(email, None)
    log_action(username, "RESET_PASSWORD", "Password reset via email OTP")
    return jsonify({"message": "Password reset successfully! You can now login."}), 200


@app.route("/api/auth/send-password", methods=["POST"])
def send_password():
    """Generate a temp password and email it to the user's registered email."""
    data  = request.json
    email = data.get("email", "").strip().lower()

    if not email:
        return jsonify({"error": "Email address is required"}), 400

    # Find user by email
    user = users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not user:
        return jsonify({"error": "This email is not registered. Please sign up first."}), 404

    username = user.get("username", "")

    # Mask email for display
    at     = email.index("@")
    masked = email[:2] + "***" + email[at:]

    # Generate a random 8-character temp password (letters + digits)
    chars    = string.ascii_letters + string.digits
    temp_pwd = "".join(random.choices(chars, k=8))

    # Hash and save the new temp password
    hashed = bcrypt.hashpw(temp_pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    users.update_one({"username": username}, {"$set": {"password": hashed}})

    # Email it
    sent = send_temp_password_email(email, username, temp_pwd)
    if not sent:
        return jsonify({"error": "Failed to send email. Check server email configuration."}), 500

    log_action(username, "SEND_TEMP_PASSWORD", f"Temp password emailed to {masked}")
    return jsonify({"message": f"A new temporary password has been sent to {masked}. Please check your inbox."}), 200


@app.route("/api/auth/signup", methods=["POST"])
def signup():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must contain at least 6 characters"}), 400

    if users.find_one({"username": username}):
        return jsonify({"error": "Username already exists"}), 400

    # First user is Admin; others choose Officer or Guard
    if users.count_documents({}) == 0:
        role = "Admin"
        status = "Active"
    else:
        requested_role = data.get("role", "Officer").strip()
        role = requested_role if requested_role in ["Officer", "Guard", "Warden"] else "Officer"
        status = "Pending"
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # Email
    email = data.get("email", "").strip()

    # Phone number (optional, default placeholder)
    phone = data.get("phone", "0000000000").strip()
    if phone and phone != "0000000000":
        if not phone.isdigit() or len(phone) != 10:
            return jsonify({"error": "Phone number must be exactly 10 digits"}), 400
    elif not phone:
        phone = "0000000000"

    user_doc = {
        "username": username,
        "password": hashed,
        "role": role,
        "status": status,
        "email": email,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    users.insert_one(user_doc)

    # Auto-add to staff
    last_staff = staff.find_one({}, sort=[("staff_id", -1)])
    if last_staff:
        try:
            last_id = int(str(last_staff["staff_id"])[1:])
            new_id = f"S{last_id + 1}"
        except (ValueError, KeyError):
            new_id = "S1001"
    else:
        new_id = "S1001"

    staff_doc = {
        "staff_id": new_id,
        "name": username,
        "role": role,
        "phone": phone,
        "shift": "Morning",
        "shift_start": "08:00",
        "shift_end": "16:00",
        "created_at": datetime.now()
    }
    staff.insert_one(staff_doc)

    return jsonify({
        "message": "Account created successfully",
        "username": username,
        "role": role
    }), 201

# Get current logged-in user's own staff record
@app.route("/api/staff/me", methods=["GET"])
def get_my_staff_profile():
    username = request.headers.get("X-User-Name", "").strip()
    if not username:
        return jsonify({"error": "Not authenticated"}), 401
    member = staff.find_one({"name": username})
    if not member:
        return jsonify({"error": "Staff record not found"}), 404
    return jsonify(serialize_doc(member))

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    try:
        user = users.find_one({"username": username})
    except Exception as e:
        print(f"Database error during login: {e}")
        return jsonify({"error": "Database connection error. Please try again."}), 503

    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    if user.get("status") == "Pending":
        return jsonify({"error": "Your account is awaiting Admin approval."}), 403

    # ── Lockout check ─────────────────────────────────────────
    locked_until = user.get("locked_until")
    if locked_until:
        try:
            if isinstance(locked_until, str):
                locked_until = datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
            if hasattr(locked_until, "tzinfo") and locked_until.tzinfo is not None:
                locked_until = locked_until.replace(tzinfo=None)
            if datetime.now() < locked_until:
                remaining = int((locked_until - datetime.now()).total_seconds() / 60) + 1
                return jsonify({
                    "error": f"Account locked due to too many failed attempts. Try again in {remaining} minute(s)."
                }), 423
        except Exception:
            pass

    user_password = str(user.get("password") or "")
    try:
        valid = bcrypt.checkpw(password.encode("utf-8"), user_password.encode("utf-8"))
    except Exception:
        valid = False

    if not valid:
        # Fallback to legacy SHA256 check (compat with JAIL.ipynb)
        import hashlib
        sha_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        if sha_hash == user_password:
            valid = True
            try:
                hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                users.update_one({"_id": user["_id"]}, {"$set": {"password": hashed}})
            except Exception:
                pass

    if not valid:
        # Increment failed attempts
        attempts = user.get("failed_attempts", 0) + 1
        update_fields = {"failed_attempts": attempts}
        if attempts >= 5:
            update_fields["locked_until"] = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                users.update_one({"_id": user["_id"]}, {"$set": update_fields})
            except Exception:
                pass
            return jsonify({
                "error": "Account locked after 5 failed attempts. Try again in 15 minutes."
            }), 423
        try:
            users.update_one({"_id": user["_id"]}, {"$set": update_fields})
        except Exception:
            pass
        remaining_attempts = 5 - attempts
        return jsonify({
            "error": f"Invalid username or password. {remaining_attempts} attempt(s) remaining before lockout."
        }), 401

    # ── Successful login — reset counters ────────────────────
    try:
        users.update_one({"_id": user["_id"]}, {"$set": {"failed_attempts": 0, "locked_until": None}})
    except Exception:
        pass

    return jsonify({
        "message": "Logged in successfully",
        "username": user["username"],
        "role": user.get("role", "Guard")
    })

# =========================================================
# ADMIN APPROVAL ENDPOINTS
# =========================================================
@app.route("/api/admin/pending_users", methods=["GET"])
def get_pending_users():
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403
    pending = list(users.find({"status": "Pending"}))
    return jsonify(serialize_list(pending))

@app.route("/api/admin/approve/<user_id>", methods=["POST"])
def approve_user(user_id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied"}), 403
    users.update_one({"_id": ObjectId(user_id)}, {"$set": {"status": "Active"}})
    return jsonify({"message": "User approved successfully"})

@app.route("/api/admin/reject/<user_id>", methods=["DELETE"])
def reject_user(user_id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied"}), 403
    user = users.find_one({"_id": ObjectId(user_id)})
    if user:
        users.delete_one({"_id": ObjectId(user_id)})
        # also delete the associated staff record that was auto-generated
        staff.delete_one({"name": user.get("username")})
    return jsonify({"message": "User rejected and deleted"})

# =========================================================
# DASHBOARD OVERVIEW
# =========================================================
@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    # Sync occupancies first
    sync_cell_occupancy()
    
    total_p = prisoners.count_documents({})
    now_date = datetime.now().date()
    
    # Calculate active prisoners
    active_p = 0
    for p in prisoners.find({}):
        try:
            rel_date = datetime.strptime(p.get("release_date", p.get("release", "")), "%Y-%m-%d").date()
            if rel_date >= now_date:
                active_p += 1
        except Exception:
            active_p += 1

    released_p = total_p - active_p

    # Get Cell occupancy stats
    occupied_cells = cells.count_documents({"status": "Occupied"})
    full_cells = cells.count_documents({"status": "FULL"})
    total_cells = cells.count_documents({})
    cell_ratio = f"{occupied_cells + full_cells}/{total_cells}" if total_cells > 0 else "0/0"

    return jsonify({
        "prisoners": total_p,
        "active_prisoners": active_p,
        "released_prisoners": released_p,
        "staff": staff.count_documents({}),
        "visitors": visitors.count_documents({}),
        "cells": cell_ratio,
        "total_cells": total_cells,
        "incidents": incidents.count_documents({})
    })

@app.route("/api/dashboard/releases", methods=["GET"])
def dashboard_releases():
    upcoming = []
    now = datetime.now()
    now_date = now.date()
    end_date = now_date + timedelta(days=30)
    
    for p in prisoners.find({}):
        release_val = p.get("release_date", p.get("release", ""))
        try:
            rdate = datetime.strptime(release_val, "%Y-%m-%d").date()
            if now_date <= rdate <= end_date:
                days_left = (rdate - now_date).days
                upcoming.append({
                    "prisoner_id": p.get("prisoner_id", ""),
                    "name": p.get("name", ""),
                    "days": days_left,
                    "release_date": release_val
                })
        except Exception:
            pass
            
    upcoming.sort(key=lambda x: x["days"])
    return jsonify(upcoming[:8])

# =========================================================
# PRISONERS ENDPOINTS (Admin and Officer)
# =========================================================
@app.route("/api/prisoners", methods=["GET"])
def get_prisoners():
    query = request.args.get("q", "").strip().lower()
    selected_status = request.args.get("status", "All")
    
    docs = list(prisoners.find().sort("name", ASCENDING))
    
    # Determine status dynamically
    now_date = datetime.now().date()
    filtered = []
    for p in docs:
        try:
            rel_date = datetime.strptime(p.get("release_date", p.get("release", "")), "%Y-%m-%d").date()
            p["status"] = "Released" if rel_date < now_date else "Active"
        except Exception:
            p["status"] = "Active"

        if selected_status != "All" and p["status"] != selected_status:
            continue

        if query:
            searchable = " ".join([
                str(p.get("prisoner_id", "")),
                str(p.get("name", "")),
                str(p.get("cell", "")),
                str(p.get("crime", ""))
            ]).lower()
            if query not in searchable:
                continue
                
        filtered.append(p)

    return jsonify(serialize_list(filtered))

@app.route("/api/prisoners", methods=["POST"])
def add_prisoner():
    if not authorize(["Admin", "Officer", "Warden"]):
        return jsonify({"error": "Access Denied: Admin, Officer or Warden role required"}), 403

    data = request.json
    name = data.get("name", "").strip()
    age_val = data.get("age")
    gender = data.get("gender", "Male").strip()
    cell = data.get("cell", "").strip()
    block = data.get("block", "").strip()
    crime = data.get("crime", "").strip()
    entry_val = data.get("entry_date", data.get("entry", "")).strip()
    days_val = data.get("sentence_days", data.get("days"))
    emergency_contact = data.get("emergency_contact", "").strip()
    contact_phone = data.get("contact_phone", "").strip()

    if not all([name, age_val, crime, cell, entry_val, days_val]):
        return jsonify({"error": "All fields are required"}), 400

    try:
        age = int(age_val)
        if age < 18 or age > 120:
            return jsonify({"error": "Age must be at least 18"}), 400
    except ValueError:
        return jsonify({"error": "Age must be a valid number"}), 400

    try:
        days = int(days_val)
        if days <= 0:
            return jsonify({"error": "Sentence days must be positive"}), 400
    except ValueError:
        return jsonify({"error": "Sentence days must be a number"}), 400

    try:
        entry_date = datetime.strptime(entry_val, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Entry date must be in YYYY-MM-DD format"}), 400

    # Cell checks
    cell_doc = cells.find_one({"cell_id": cell})
    if not cell_doc:
        return jsonify({"error": f"Cell {cell} does not exist. Add the cell first."}), 400
    sync_cell_occupancy()
    cell_doc = cells.find_one({"cell_id": cell})
    occupied = int(cell_doc.get("occupied", 0) or 0)
    capacity = int(cell_doc.get("capacity", 0) or 0)
    if occupied >= capacity:
        return jsonify({"error": f"Cell {cell} is full ({occupied}/{capacity})."}), 400

    release_date = entry_date + timedelta(days=days)

    # Next ID auto generator
    last_prisoner = prisoners.find_one({}, sort=[("prisoner_id", -1)])
    if last_prisoner:
        try:
            last_id = int(str(last_prisoner["prisoner_id"])[1:])
            new_id = f"P{last_id + 1}"
        except (ValueError, KeyError):
            new_id = "P1001"
    else:
        new_id = "P1001"

    prisoner_doc = {
        "prisoner_id": new_id,
        "name": name,
        "age": age,
        "gender": gender,
        "cell": cell,
        "block": block,
        "crime": crime,
        "entry_date": entry_date.strftime("%Y-%m-%d"),
        "release_date": release_date.strftime("%Y-%m-%d"),
        "sentence_days": days,
        "entry": entry_date.strftime("%Y-%m-%d"),
        "release": release_date.strftime("%Y-%m-%d"),
        "emergency_contact": emergency_contact,
        "contact_phone": contact_phone,
        "photo": data.get("photo", ""),
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }

    prisoners.insert_one(prisoner_doc)
    sync_cell_occupancy()
    
    log_action(get_user_from_headers(), "ADD_PRISONER", f"{new_id} - {name}")
    return jsonify({"message": f"Prisoner {new_id} added successfully", "prisoner": serialize_doc(prisoner_doc)}), 201

@app.route("/api/prisoners/<prisoner_id>", methods=["PUT"])
def update_prisoner(prisoner_id):
    if not authorize(["Admin", "Officer", "Warden"]):
        return jsonify({"error": "Access Denied: Admin, Officer or Warden role required"}), 403

    p = prisoners.find_one({"prisoner_id": prisoner_id})
    if not p:
        return jsonify({"error": "Prisoner not found"}), 404

    data = request.json
    name = data.get("name", "").strip()
    age_val = data.get("age")
    gender = data.get("gender", "Male").strip()
    cell = data.get("cell", "").strip()
    block = data.get("block", "").strip()
    crime = data.get("crime", "").strip()
    entry_val = data.get("entry_date", data.get("entry", "")).strip()
    days_val = data.get("sentence_days", data.get("days"))
    emergency_contact = data.get("emergency_contact", "").strip()
    contact_phone = data.get("contact_phone", "").strip()

    if not all([name, age_val, crime, cell, entry_val, days_val]):
        return jsonify({"error": "All fields are required"}), 400

    try:
        age = int(age_val)
        if age < 18 or age > 120:
            return jsonify({"error": "Age must be at least 18"}), 400
    except ValueError:
        return jsonify({"error": "Age must be a valid number"}), 400

    try:
        days = int(days_val)
        if days <= 0:
            return jsonify({"error": "Sentence days must be positive"}), 400
    except ValueError:
        return jsonify({"error": "Sentence days must be a number"}), 400

    try:
        entry_date = datetime.strptime(entry_val, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Entry date must be in YYYY-MM-DD format"}), 400

    # Cell checks
    old_cell = str(p.get("cell", "")).strip()
    if cell != old_cell:
        cell_doc = cells.find_one({"cell_id": cell})
        if not cell_doc:
            return jsonify({"error": f"Cell {cell} does not exist. Add the cell first."}), 400
        sync_cell_occupancy()
        cell_doc = cells.find_one({"cell_id": cell})
        occupied = int(cell_doc.get("occupied", 0) or 0)
        capacity = int(cell_doc.get("capacity", 0) or 0)
        if occupied >= capacity:
            return jsonify({"error": f"Cell {cell} is full ({occupied}/{capacity})."}), 400

    release_date = entry_date + timedelta(days=days)

    updated_data = {
        "name": name,
        "age": age,
        "gender": gender,
        "cell": cell,
        "block": block,
        "crime": crime,
        "entry_date": entry_date.strftime("%Y-%m-%d"),
        "release_date": release_date.strftime("%Y-%m-%d"),
        "sentence_days": days,
        "entry": entry_date.strftime("%Y-%m-%d"),
        "release": release_date.strftime("%Y-%m-%d"),
        "emergency_contact": emergency_contact,
        "contact_phone": contact_phone,
        "updated_at": datetime.now()
    }
    # Only update photo if a new one was provided
    incoming_photo = data.get("photo", "")
    if incoming_photo:
        updated_data["photo"] = incoming_photo

    prisoners.update_one({"prisoner_id": prisoner_id}, {"$set": updated_data})
    sync_cell_occupancy()
    
    log_action(get_user_from_headers(), "EDIT_PRISONER", prisoner_id)
    return jsonify({"message": "Prisoner updated successfully"})

@app.route("/api/prisoners/<prisoner_id>", methods=["DELETE"])
def delete_prisoner(prisoner_id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    result = prisoners.delete_one({"prisoner_id": prisoner_id})
    if result.deleted_count == 0:
        return jsonify({"error": "Prisoner not found"}), 404

    sync_cell_occupancy()
    log_action(get_user_from_headers(), "DELETE_PRISONER", prisoner_id)
    return jsonify({"message": "Prisoner deleted successfully"})

# =========================================================
# STAFF ENDPOINTS (Admin Only)
# =========================================================
@app.route("/api/staff", methods=["GET"])
def get_staff():
    query = request.args.get("q", "").strip().lower()
    selected_role = request.args.get("role", "All")

    docs = list(staff.find().sort("name", ASCENDING))
    filtered = []
    
    for member in docs:
        name = str(member.get("name", "")).lower()
        role = str(member.get("role", ""))
        phone = str(member.get("phone", ""))
        staff_id = str(member.get("staff_id", "")).lower()

        if selected_role != "All" and role != selected_role:
            continue

        if query:
            if query not in name and query not in role.lower() and query not in phone and query not in staff_id:
                continue

        filtered.append(member)

    return jsonify(serialize_list(filtered))

@app.route("/api/staff", methods=["POST"])
def add_staff():
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    data = request.json
    name = data.get("name", "").strip()
    role = data.get("role", "Guard")
    phone = data.get("phone", "").strip()
    shift = data.get("shift", "Morning")
    start = data.get("shift_start", "").strip()
    end = data.get("shift_end", "").strip()

    if not all([name, role, phone, shift, start, end]):
        return jsonify({"error": "All fields are required"}), 400

    if not phone.isdigit() or len(phone) != 10:
        return jsonify({"error": "Phone number must contain exactly 10 digits"}), 400

    # Auto-generate ID: S1001, S1002...
    last_staff = staff.find_one({}, sort=[("staff_id", -1)])
    if last_staff:
        try:
            last_id = int(str(last_staff["staff_id"])[1:])
            new_id = f"S{last_id + 1}"
        except (ValueError, KeyError):
            new_id = "S1001"
    else:
        new_id = "S1001"

    staff_doc = {
        "staff_id": new_id,
        "name": name,
        "role": role,
        "phone": phone,
        "shift": shift,
        "shift_start": start,
        "shift_end": end,
        "created_at": datetime.now()
    }

    staff.insert_one(staff_doc)
    log_action(get_user_from_headers(), "ADD_STAFF", new_id)
    return jsonify({"message": "Staff member added successfully", "member": serialize_doc(staff_doc)}), 201

@app.route("/api/staff/<id>", methods=["PUT"])
def update_staff(id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    data = request.json
    name = data.get("name", "").strip()
    role = data.get("role")
    phone = data.get("phone", "").strip()
    shift = data.get("shift")
    start = data.get("shift_start", "").strip()
    end = data.get("shift_end", "").strip()

    if not all([name, role, phone, shift, start, end]):
        return jsonify({"error": "All fields are required"}), 400

    if not phone.isdigit() or len(phone) != 10:
        return jsonify({"error": "Phone number must contain exactly 10 digits"}), 400

    updated_data = {
        "name": name,
        "role": role,
        "phone": phone,
        "shift": shift,
        "shift_start": start,
        "shift_end": end
    }

    result = staff.update_one({"_id": ObjectId(id)}, {"$set": updated_data})
    if result.matched_count == 0:
        return jsonify({"error": "Staff member not found"}), 404

    log_action(get_user_from_headers(), "EDIT_STAFF", str(id))
    return jsonify({"message": "Staff member updated successfully"})

@app.route("/api/staff/<id>", methods=["DELETE"])
def delete_staff(id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    s = staff.find_one({"_id": ObjectId(id)})
    if not s:
        return jsonify({"error": "Staff member not found"}), 404

    staff.delete_one({"_id": ObjectId(id)})
    log_action(get_user_from_headers(), "DELETE_STAFF", s.get("staff_id", str(id)))
    return jsonify({"message": "Staff member deleted successfully"})

# =========================================================
# VISITORS ENDPOINTS (All Roles)
# =========================================================
@app.route("/api/visitors", methods=["GET"])
def get_visitors():
    query = request.args.get("q", "").strip().lower()
    
    docs = list(visitors.find().sort([("visit_date", -1)]))
    filtered = []
    
    for v in docs:
        pid = str(v.get("prisoner", ""))
        pname = v.get("prisoner_name", "")
        
        searchable = " ".join([
            str(v.get("visit_id", "")),
            str(v.get("visitor", "")),
            pid,
            pname,
            str(v.get("relation", "")),
            str(v.get("phone", ""))
        ]).lower()

        if query and query not in searchable:
            continue

        filtered.append(v)

    return jsonify(serialize_list(filtered))

@app.route("/api/visitors", methods=["POST"])
def add_visitor():
    if not authorize(["Admin", "Officer", "Warden", "Guard"]):
        return jsonify({"error": "Access Denied"}), 403

    data = request.json
    name = data.get("visitor", "").strip()
    prisoner = data.get("prisoner", "").strip()
    relation = data.get("relation", "").strip()
    phone = data.get("phone", "").strip()
    visit_date = data.get("visit_date", "").strip()
    visit_time = data.get("visit_time", "").strip()

    if not name or not prisoner or not relation:
        return jsonify({"error": "Visitor name, Prisoner ID and Relation fields are required"}), 400

    prisoner_doc = prisoners.find_one({"prisoner_id": prisoner})
    if not prisoner_doc:
        return jsonify({"error": f"Prisoner ID {prisoner} does not exist in register."}), 400

    if phone and (not phone.isdigit() or len(phone) != 10):
        return jsonify({"error": "Phone number must be a valid 10-digit number"}), 400

    if not visit_date:
        visit_date = datetime.now().strftime("%Y-%m-%d")
    if not visit_time:
        visit_time = datetime.now().strftime("%H:%M")

    # Auto-generate ID: V1001, V1002...
    last_visitor = visitors.find_one({}, sort=[("visit_id", -1)])
    if last_visitor:
        try:
            last_id = int(str(last_visitor["visit_id"])[1:])
            new_id = f"V{last_id + 1}"
        except (ValueError, KeyError):
            new_id = "V1001"
    else:
        new_id = "V1001"

    visitor_doc = {
        "visit_id": new_id,
        "visitor": name,
        "prisoner": prisoner,
        "prisoner_name": prisoner_doc.get("name", ""),
        "relation": relation,
        "phone": phone,
        "visit_date": visit_date,
        "visit_time": visit_time,
        "created_at": datetime.now()
    }

    visitors.insert_one(visitor_doc)
    log_action(get_user_from_headers(), "ADD_VISIT", f"{new_id} - {name} to {prisoner}")
    return jsonify({"message": f"Visitor visit {new_id} logged successfully", "visitor": serialize_doc(visitor_doc)}), 201

@app.route("/api/visitors/<id>", methods=["PUT"])
def update_visitor(id):
    if not authorize(["Admin", "Officer", "Warden", "Guard"]):
        return jsonify({"error": "Access Denied"}), 403

    data = request.json
    name = data.get("visitor", "").strip()
    prisoner = data.get("prisoner", "").strip()
    relation = data.get("relation", "").strip()
    phone = data.get("phone", "").strip()
    visit_date = data.get("visit_date", "").strip()
    visit_time = data.get("visit_time", "").strip()

    if not name or not prisoner or not relation:
        return jsonify({"error": "Fields are required"}), 400

    prisoner_doc = prisoners.find_one({"prisoner_id": prisoner})
    if not prisoner_doc:
        return jsonify({"error": f"Prisoner ID {prisoner} does not exist."}), 400

    updated_data = {
        "visitor": name,
        "prisoner": prisoner,
        "prisoner_name": prisoner_doc.get("name", ""),
        "relation": relation,
        "phone": phone,
        "visit_date": visit_date,
        "visit_time": visit_time
    }

    result = visitors.update_one({"_id": ObjectId(id)}, {"$set": updated_data})
    if result.matched_count == 0:
        return jsonify({"error": "Visitor record not found"}), 404

    log_action(get_user_from_headers(), "EDIT_VISITOR", str(id))
    return jsonify({"message": "Visitor record updated successfully"})

@app.route("/api/visitors/<id>", methods=["DELETE"])
def delete_visitor(id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    v = visitors.find_one({"_id": ObjectId(id)})
    if not v:
        return jsonify({"error": "Visitor record not found"}), 404

    visitors.delete_one({"_id": ObjectId(id)})
    log_action(get_user_from_headers(), "DELETE_VISITOR", v.get("visit_id", str(id)))
    return jsonify({"message": "Visitor record deleted successfully"})

# =========================================================
# CELLS ENDPOINTS
# =========================================================
@app.route("/api/cells", methods=["GET"])
def get_cells():
    sync_cell_occupancy()
    docs = list(cells.find().sort("cell_id", ASCENDING))
    return jsonify(serialize_list(docs))

@app.route("/api/cells", methods=["POST"])
def add_cell():
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    data = request.json
    cell_id = data.get("cell_id", "").strip()
    block = data.get("block", "").strip()
    floor = data.get("floor", "").strip()
    capacity_val = data.get("capacity")

    if not all([cell_id, block, floor, capacity_val]):
        return jsonify({"error": "All fields are required"}), 400

    try:
        capacity = int(capacity_val)
        if capacity <= 0:
            return jsonify({"error": "Capacity must be positive"}), 400
    except ValueError:
        return jsonify({"error": "Capacity must be a valid number"}), 400

    if cells.find_one({"cell_id": cell_id}):
        return jsonify({"error": "Cell ID already exists"}), 400

    cell_doc = {
        "cell_id": cell_id,
        "block": block,
        "floor": floor,
        "capacity": capacity,
        "occupied": 0,
        "available": capacity,
        "status": "Available",
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }

    cells.insert_one(cell_doc)
    sync_cell_occupancy()
    log_action(get_user_from_headers(), "ADD_CELL", cell_id)
    return jsonify({"message": f"Cell {cell_id} added successfully", "cell": serialize_doc(cell_doc)}), 201

@app.route("/api/cells/<id>", methods=["PUT"])
def update_cell(id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    data = request.json
    block = data.get("block", "").strip()
    floor = data.get("floor", "").strip()
    capacity_val = data.get("capacity")

    if not all([block, floor, capacity_val]):
        return jsonify({"error": "Block, floor, and capacity fields are required"}), 400

    try:
        capacity = int(capacity_val)
        if capacity <= 0:
            return jsonify({"error": "Capacity must be positive"}), 400
    except ValueError:
        return jsonify({"error": "Capacity must be a valid number"}), 400

    updated_data = {
        "block": block,
        "floor": floor,
        "capacity": capacity
    }

    result = cells.update_one({"_id": ObjectId(id)}, {"$set": updated_data})
    if result.matched_count == 0:
        return jsonify({"error": "Cell not found"}), 404

    sync_cell_occupancy()
    log_action(get_user_from_headers(), "EDIT_CELL", str(id))
    return jsonify({"message": "Cell updated successfully"})

@app.route("/api/cells/<id>", methods=["DELETE"])
def delete_cell(id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403

    c = cells.find_one({"_id": ObjectId(id)})
    if not c:
        return jsonify({"error": "Cell not found"}), 404

    cells.delete_one({"_id": ObjectId(id)})
    log_action(get_user_from_headers(), "DELETE_CELL", c.get("cell_id", str(id)))
    return jsonify({"message": "Cell deleted successfully"})

# =========================================================
# INCIDENTS ENDPOINTS
# =========================================================
@app.route("/api/incidents", methods=["GET"])
def get_incidents():
    docs = list(incidents.find().sort("date", DESCENDING))
    return jsonify(serialize_list(docs))

@app.route("/api/incidents", methods=["POST"])
def add_incident():
    if not authorize(["Admin", "Officer", "Warden"]):
        return jsonify({"error": "Access Denied: Admin, Officer or Warden required"}), 403

    data = request.json
    date_val = data.get("date", "").strip()
    itype = data.get("type", "Other").strip()
    location = data.get("location", "").strip()
    prisoner = data.get("prisoner", "").strip()
    status = data.get("status", "Open").strip()
    description = data.get("description", "").strip()

    if not location:
        return jsonify({"error": "Location is required"}), 400

    if prisoner:
        if not prisoners.find_one({"prisoner_id": prisoner}):
            return jsonify({"error": f"Prisoner ID {prisoner} does not exist."}), 400

    if not date_val:
        date_val = datetime.now().strftime("%Y-%m-%d")

    # Auto-generate ID: I1001, I1002...
    last_incident = incidents.find_one({}, sort=[("incident_id", -1)])
    if last_incident:
        try:
            last_id = int(str(last_incident["incident_id"])[1:])
            new_id = f"I{last_id + 1}"
        except (ValueError, KeyError):
            new_id = "I1001"
    else:
        new_id = "I1001"

    inc_doc = {
        "incident_id": new_id,
        "date": date_val,
        "type": itype,
        "location": location,
        "prisoner": prisoner,
        "status": status,
        "description": description,
        "reported_by": get_user_from_headers(),
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }

    incidents.insert_one(inc_doc)
    log_action(get_user_from_headers(), "ADD_INCIDENT", f"{new_id} - {itype}")
    return jsonify({"message": f"Incident {new_id} reported successfully", "incident": serialize_doc(inc_doc)}), 201

@app.route("/api/incidents/<id>", methods=["PUT"])
def update_incident(id):
    if not authorize(["Admin", "Officer", "Warden"]):
        return jsonify({"error": "Access Denied: Admin, Officer or Warden required"}), 403

    data = request.json
    date_val = data.get("date", "").strip()
    itype = data.get("type").strip()
    location = data.get("location", "").strip()
    prisoner = data.get("prisoner", "").strip()
    status = data.get("status").strip()
    description = data.get("description", "").strip()

    if not location:
        return jsonify({"error": "Location is required"}), 400

    if prisoner:
        if not prisoners.find_one({"prisoner_id": prisoner}):
            return jsonify({"error": f"Prisoner ID {prisoner} does not exist."}), 400

    updated_data = {
        "date": date_val,
        "type": itype,
        "location": location,
        "prisoner": prisoner,
        "status": status,
        "description": description,
        "updated_at": datetime.now()
    }

    result = incidents.update_one({"_id": ObjectId(id)}, {"$set": updated_data})
    if result.matched_count == 0:
        return jsonify({"error": "Incident report not found"}), 404

    log_action(get_user_from_headers(), "EDIT_INCIDENT", str(id))
    return jsonify({"message": "Incident report updated successfully"})

@app.route("/api/incidents/<id>", methods=["DELETE"])
def delete_incident(id):
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin required"}), 403

    i = incidents.find_one({"_id": ObjectId(id)})
    if not i:
        return jsonify({"error": "Incident report not found"}), 404

    incidents.delete_one({"_id": ObjectId(id)})
    log_action(get_user_from_headers(), "DELETE_INCIDENT", i.get("incident_id", str(id)))
    return jsonify({"message": "Incident report deleted successfully"})

# =========================================================
# AUDIT LOGS ENDPOINT (Admin Only)
# =========================================================
@app.route("/api/logs", methods=["GET"])
def get_logs():
    if not authorize(["Admin"]):
        return jsonify({"error": "Access Denied: Admin role required"}), 403
    docs = list(audit_logs.find().sort("timestamp", -1).limit(500))
    return jsonify(serialize_list(docs))

# =========================================================
# START APPLICATION
# =========================================================
if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "True").lower() == "true"
    app.run(host=host, port=port, debug=debug)
