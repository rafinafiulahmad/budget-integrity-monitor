"""
auth_org_backend.py — Auth & Org extensions for Budget Integrity Monitor (v7)

Author  : Nafiul Ahmad Rafi
          Civic Tech Architect & Developer | AI Policy Researcher
          Director, Insights Bangladesh | NagorikBD | AtlasAii
License : See LICENSE_AUTH_ORG.txt in this directory

Implements backend for all 7 Auth & Org features:
  1. Password reset flow              (request -> OTP email -> new password)
  2. Invite-by-email user onboarding  (admin sends link; invitee sets own password)
  3. Role-based tab visibility        (no new endpoints; enforced via decorators)
  4. Org-level settings               (GET/POST /api/org/settings)
  5. Multi-org super-admin            (superadmin role + /api/superadmin/*)
  6. SSO / OAuth2 (Google login)      (/api/auth/google + /callback)
  7. Audit log                        (/api/audit + audit() helper)

INSTALLATION
------------
1. Place this file next to auth.py  (already done if you see this file)
2. pip install authlib               (for Google SSO only — optional)
3. Set env vars (see SSO section below)
4. In app.py, after existing imports, add:

     from auth_org_backend import register_routes, audit, require_superadmin
     register_routes(app)

5. Add audit() calls at each action site in app.py (see examples at bottom).

NEW ENDPOINTS (all registered by register_routes)
--------------------------------------------------
POST /api/auth/reset-request
POST /api/auth/reset-verify
POST /api/auth/reset-confirm
POST /api/auth/invite                [admin]
GET  /api/auth/invite/<token>
POST /api/auth/invite/<token>/accept
GET  /api/org/settings               [auth]
POST /api/org/settings               [admin]
GET  /api/superadmin/orgs            [superadmin]
POST /api/superadmin/orgs            [superadmin]
GET  /api/superadmin/orgs/<org>/users         [superadmin]
POST /api/superadmin/orgs/<org>/users/<u>/role [superadmin]
GET  /api/auth/google
GET  /api/auth/google/callback
GET  /api/audit                      [admin]
GET  /accept-invite                  (SPA redirect)

SSO ENV VARS
------------
GOOGLE_CLIENT_ID        — from Google Cloud Console
GOOGLE_CLIENT_SECRET    — from Google Cloud Console
GOOGLE_ALLOWED_DOMAINS  — comma-separated e.g. "pibd.org,undp.org" (empty = allow all)
BIM_BASE_URL            — e.g. "https://app.pibd.org"
BIM_SECRET_KEY          — Flask secret key (auto-generated if absent)

SMTP ENV VARS
-------------
BIM_SMTP_HOST   — falls back to alerting.py email settings if absent
BIM_SMTP_PORT   — default 587
BIM_SMTP_USER
BIM_SMTP_PASS
BIM_SMTP_FROM
"""

import hashlib
import json
import os
import re
import secrets
import smtplib
import datetime
from email.mime.text import MIMEText
from functools import wraps
from pathlib import Path

from flask import request, jsonify, redirect, url_for, g


# ── Storage paths (inherit from auth.py structure) ────────────────────────────

AUTH_DIR          = Path("reports/auth")
AUTH_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE        = AUTH_DIR / "users.json"
SESSIONS_FILE     = AUTH_DIR / "sessions.json"
RESET_TOKENS_FILE = AUTH_DIR / "reset_tokens.json"
INVITES_FILE      = AUTH_DIR / "invites.json"
ORG_SETTINGS_DIR  = AUTH_DIR / "org_settings"
AUDIT_LOG_FILE    = AUTH_DIR / "audit.jsonl"

ORG_SETTINGS_DIR.mkdir(exist_ok=True)

RESET_TTL_MIN = 15   # minutes OTP is valid
INVITE_TTL_H  = 48   # hours invite link is valid


# ── Storage helpers ───────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))

def _hash(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()

def _now_iso() -> str:
    return _now().isoformat()

def _expired(iso: str) -> bool:
    return _now().isoformat() > iso


# =============================================================================
# 7. AUDIT LOG  (defined first — used by all other features)
# =============================================================================

def audit(user: str, org: str, action: str, detail: str = "") -> None:
    """
    Append one audit entry to AUDIT_LOG_FILE (JSONL format).

    Call this from app.py at every significant action:
        from auth_org_backend import audit
        audit(g.user, g.org, "analyze", project.name)

    Standard action strings:
        login, logout, register, analyze, bulk_import,
        pdf_export, xlsx_export, invite_sent, invite_accepted,
        org_settings_update, role_change, password_reset_requested,
        password_reset_confirmed, superadmin_create_org
    """
    entry = json.dumps({
        "timestamp": _now_iso(),
        "user":      user,
        "org":       org,
        "action":    action,
        "detail":    detail,
        "ip":        _client_ip(),
    })
    with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def _client_ip() -> str:
    try:
        return request.headers.get("X-Forwarded-For", request.remote_addr or "")
    except RuntimeError:
        return ""


def _read_audit(
    org: str,
    page: int = 1,
    limit: int = 50,
    user_filter: str = "",
    action_filter: str = "",
) -> dict:
    """Return paginated, filtered audit entries for one org (newest first)."""
    if not AUDIT_LOG_FILE.exists():
        return {"entries": [], "total": 0}
    lines   = AUDIT_LOG_FILE.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("org") != org:
            continue
        if user_filter   and user_filter   not in e.get("user", ""):
            continue
        if action_filter and e.get("action") != action_filter:
            continue
        entries.append(e)
    total = len(entries)
    start = (page - 1) * limit
    return {"entries": entries[start : start + limit], "total": total}


# =============================================================================
# 1. PASSWORD RESET FLOW
# =============================================================================
#
# POST /api/auth/reset-request   { email }               → sends OTP
# POST /api/auth/reset-verify    { token }               → validates OTP
# POST /api/auth/reset-confirm   { token, new_password } → changes password

def _find_user_by_email(email: str):
    users = _load(USERS_FILE)
    for username, rec in users.items():
        if rec.get("email", "").lower() == email.lower():
            return username, rec
    return None, None


def _send_reset_email(to_email: str, otp: str) -> None:
    subject = "Budget Integrity Monitor — password reset code"
    body    = (
        f"Your one-time reset code is: {otp}\n\n"
        f"It expires in {RESET_TTL_MIN} minutes.\n\n"
        "If you did not request a password reset, ignore this email."
    )
    _send_email(to_email, subject, body)


def request_password_reset(email: str) -> dict:
    """Generate OTP, store with TTL, email it. Always returns ok (no user enumeration)."""
    username, user = _find_user_by_email(email)
    if not username:
        return {"ok": True}   # don't reveal whether email exists
    otp    = secrets.token_hex(3).upper()   # 6-char hex OTP
    tokens = _load(RESET_TOKENS_FILE)
    tokens[otp] = {
        "username": username,
        "expires":  (_now() + datetime.timedelta(minutes=RESET_TTL_MIN)).isoformat(),
    }
    _save(RESET_TOKENS_FILE, tokens)
    try:
        _send_reset_email(email, otp)
    except Exception as exc:
        print(f"[WARN] reset email failed: {exc}")
    audit(username, user.get("org", ""), "password_reset_requested", email)
    return {"ok": True}


def verify_reset_token(token: str) -> dict:
    tokens = _load(RESET_TOKENS_FILE)
    rec    = tokens.get(token.upper())
    if not rec:
        return {"error": "Invalid or expired reset code."}
    if _expired(rec["expires"]):
        tokens.pop(token.upper())
        _save(RESET_TOKENS_FILE, tokens)
        return {"error": "Reset code has expired. Please request a new one."}
    return {"ok": True, "username": rec["username"]}


def confirm_reset(token: str, new_password: str) -> dict:
    result = verify_reset_token(token)
    if "error" in result:
        return result
    if len(new_password) < 6:
        return {"error": "Password must be at least 6 characters."}
    users    = _load(USERS_FILE)
    username = result["username"]
    user     = users.get(username)
    if not user:
        return {"error": "User not found."}
    salt                   = secrets.token_hex(8)
    user["password_hash"]  = _hash(new_password, salt)
    user["salt"]           = salt
    _save(USERS_FILE, users)
    tokens = _load(RESET_TOKENS_FILE)
    tokens.pop(token.upper(), None)
    _save(RESET_TOKENS_FILE, tokens)
    audit(username, user.get("org", ""), "password_reset_confirmed")
    return {"ok": True}


# =============================================================================
# 2. INVITE-BY-EMAIL ONBOARDING
# =============================================================================
#
# POST /api/auth/invite                 [admin] { email, role }
# GET  /api/auth/invite/<token>                → { org, email, role }
# POST /api/auth/invite/<token>/accept         { username, password }

def _send_invite_email(to_email: str, org: str, token: str) -> None:
    base_url = os.environ.get("BIM_BASE_URL", "http://localhost:5050")
    link     = f"{base_url}/accept-invite?token={token}"
    subject  = f"You're invited to {org} on Budget Integrity Monitor"
    body     = (
        f"You have been invited to join {org} on Budget Integrity Monitor.\n\n"
        f"Click the link below to create your account (valid for {INVITE_TTL_H} hours):\n"
        f"{link}\n\n"
        "If you did not expect this invitation, you can safely ignore this email."
    )
    _send_email(to_email, subject, body)


def create_invite(email: str, role: str, org: str, invited_by: str) -> dict:
    if role not in ("viewer", "analyst", "admin"):
        return {"error": "Invalid role. Must be viewer, analyst, or admin."}
    token   = secrets.token_urlsafe(32)
    invites = _load(INVITES_FILE)
    invites[token] = {
        "email":      email,
        "role":       role,
        "org":        org,
        "invited_by": invited_by,
        "expires":    (_now() + datetime.timedelta(hours=INVITE_TTL_H)).isoformat(),
        "accepted":   False,
    }
    _save(INVITES_FILE, invites)
    try:
        _send_invite_email(email, org, token)
    except Exception as exc:
        print(f"[WARN] invite email failed: {exc}")
    audit(invited_by, org, "invite_sent", f"{email} ({role})")
    return {"ok": True, "token": token}


def get_invite(token: str) -> dict:
    invites = _load(INVITES_FILE)
    inv     = invites.get(token)
    if not inv:
        return {"error": "Invalid invite link."}
    if inv.get("accepted"):
        return {"error": "This invite has already been used."}
    if _expired(inv["expires"]):
        return {"error": "This invite has expired. Ask your admin to resend it."}
    return {"org": inv["org"], "email": inv["email"], "role": inv["role"]}


def accept_invite(token: str, username: str, password: str) -> dict:
    info = get_invite(token)
    if "error" in info:
        return info
    from auth import register_user, login_user
    result = register_user(username, password, info["org"], info["role"])
    if "error" in result:
        return result
    invites = _load(INVITES_FILE)
    if token in invites:
        invites[token]["accepted"]    = True
        invites[token]["accepted_by"] = username
        _save(INVITES_FILE, invites)
    audit(username, info["org"], "invite_accepted", info["email"])
    return login_user(username, password)


# =============================================================================
# 4. ORG-LEVEL SETTINGS
# =============================================================================
#
# GET  /api/org/settings  [auth]  → current org settings dict
# POST /api/org/settings  [admin] { currency, sector, country, ... }

def _org_settings_path(org: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", org)
    return ORG_SETTINGS_DIR / f"{safe}.json"


def load_org_settings(org: str) -> dict:
    path     = _org_settings_path(org)
    defaults = {
        "currency":  "USD",
        "sector":    "Infrastructure",
        "country":   "Bangladesh",
        "timezone":  "UTC+6",
        "logo_url":  "",
    }
    if not path.exists():
        return defaults
    try:
        return {**defaults, **json.loads(path.read_text())}
    except Exception:
        return defaults


def save_org_settings(org: str, data: dict) -> None:
    allowed = {"currency", "sector", "country", "timezone", "logo_url"}
    cleaned = {k: v for k, v in data.items() if k in allowed}
    _org_settings_path(org).write_text(json.dumps(cleaned, indent=2))


# =============================================================================
# 5. MULTI-ORG SUPER-ADMIN
# =============================================================================
#
# GET  /api/superadmin/orgs                              [superadmin]
# GET  /api/superadmin/orgs/<org>/users                  [superadmin]
# POST /api/superadmin/orgs/<org>/users/<user>/role      [superadmin] { role }
# POST /api/superadmin/orgs                              [superadmin] { name }

def require_superadmin(f):
    """
    Decorator: like require_admin but checks role == 'superadmin'.
    Sets g.user, g.org, g.role.
    """
    from auth import _get_token, get_session
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = _get_token()
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        sess = get_session(token)
        if not sess:
            return jsonify({"error": "Session expired or invalid"}), 401
        if sess.get("role") != "superadmin":
            return jsonify({"error": "Super-admin access required"}), 403
        g.user = sess["username"]
        g.org  = sess["org"]
        g.role = sess["role"]
        return f(*args, **kwargs)
    return wrapped


def list_all_orgs() -> list:
    """Return stats for every org found in users.json + org_history."""
    from tools.org_history import list_projects
    users   = _load(USERS_FILE)
    org_map = {}
    for username, rec in users.items():
        org = rec.get("org", "")
        if org not in org_map:
            org_map[org] = {"name": org, "user_count": 0, "project_count": 0}
        org_map[org]["user_count"] += 1
    for org in list(org_map.keys()):
        try:
            org_map[org]["project_count"] = len(list_projects(org))
        except Exception:
            pass
    return sorted(org_map.values(), key=lambda o: o["name"])


def list_users_in_org_all(org: str) -> list:
    users = _load(USERS_FILE)
    return [
        {"username": u, "role": d["role"], "created": d.get("created", "")}
        for u, d in users.items()
        if d.get("org") == org
    ]


def set_user_role(username: str, role: str) -> dict:
    if role not in ("viewer", "analyst", "admin", "superadmin"):
        return {"error": "Invalid role."}
    users = _load(USERS_FILE)
    if username not in users:
        return {"error": "User not found."}
    users[username]["role"] = role
    _save(USERS_FILE, users)
    return {"ok": True}


def create_org(name: str) -> dict:
    name = name.strip()
    if not name:
        return {"error": "Organisation name is required."}
    save_org_settings(name, {})   # creates the settings file, registering the org
    return {"ok": True, "name": name}


# =============================================================================
# 6. SSO / OAUTH2 (GOOGLE LOGIN)
# =============================================================================
#
# GET /api/auth/google           → redirects to Google consent screen
# GET /api/auth/google/callback  → handles code exchange, issues BIM token
#
# Required env vars:
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
#   GOOGLE_ALLOWED_DOMAINS  e.g. "pibd.org,undp.org"  (empty = allow all)
#   BIM_SECRET_KEY          Flask session secret
#   BIM_BASE_URL            e.g. "https://app.pibd.org"

def _init_google_oauth(app):
    """Initialise Authlib Google OAuth2 client. Returns None if authlib not installed."""
    try:
        from authlib.integrations.flask_client import OAuth
    except ImportError:
        print("[WARN] authlib not installed — Google SSO disabled. Run: pip install authlib")
        return None
    app.secret_key = os.environ.get("BIM_SECRET_KEY", secrets.token_hex(16))
    oauth  = OAuth(app)
    google = oauth.register(
        name                = "google",
        client_id           = os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret       = os.environ.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs       = {"scope": "openid email profile"},
    )
    return google


def _google_callback_logic(google):
    try:
        token_data = google.authorize_access_token()
    except Exception as exc:
        return redirect("/?sso_error=" + str(exc))

    userinfo = token_data.get("userinfo") or google.userinfo()
    email    = userinfo.get("email", "")
    domain   = email.split("@")[-1] if "@" in email else ""

    allowed_domains = [
        d.strip()
        for d in os.environ.get("GOOGLE_ALLOWED_DOMAINS", "").split(",")
        if d.strip()
    ]
    if allowed_domains and domain not in allowed_domains:
        return redirect(f"/?sso_error=domain_not_allowed&domain={domain}")

    # Find or auto-create user
    users    = _load(USERS_FILE)
    username = None
    for u, rec in users.items():
        if rec.get("email", "").lower() == email.lower():
            username = u
            break

    if not username:
        from auth import register_user
        base    = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower()) or "user"
        username = base
        counter  = 1
        while username in users:
            username = f"{base}{counter}"
            counter += 1
        register_user(username, secrets.token_hex(16), domain, "analyst")
        users = _load(USERS_FILE)
        users[username]["email"] = email
        _save(USERS_FILE, users)

    # Issue BIM session
    bim_token = secrets.token_hex(32)
    sessions  = _load(SESSIONS_FILE)
    rec       = users.get(username, {})
    sessions[bim_token] = {
        "username": username,
        "org":      rec.get("org", domain),
        "role":     rec.get("role", "analyst"),
        "expires":  (_now() + datetime.timedelta(hours=24)).isoformat(),
    }
    _save(SESSIONS_FILE, sessions)
    audit(username, rec.get("org", domain), "login", "google_sso")
    return redirect(f"/?token={bim_token}")


# =============================================================================
# SMTP HELPER
# =============================================================================

def _send_email(to: str, subject: str, body: str) -> None:
    """
    Send a plain-text email.
    Priority: BIM_SMTP_* env vars → alerting.py email settings → default gmail SMTP.
    """
    smtp_host = os.environ.get("BIM_SMTP_HOST")
    smtp_port = int(os.environ.get("BIM_SMTP_PORT", 587))
    smtp_user = os.environ.get("BIM_SMTP_USER")
    smtp_pass = os.environ.get("BIM_SMTP_PASS")
    from_addr = os.environ.get("BIM_SMTP_FROM", smtp_user or "monitor@example.com")

    if not smtp_host:
        try:
            from tools.alerting import load_settings as _las
            em        = _las().get("email", {})
            smtp_host = em.get("smtp_host", "smtp.gmail.com")
            smtp_port = int(em.get("smtp_port", 587))
            smtp_user = em.get("username", "")
            smtp_pass = em.get("password", "")
            from_addr = em.get("from_addr", smtp_user)
        except Exception:
            smtp_host = "smtp.gmail.com"

    msg            = MIMEText(body, "plain")
    msg["From"]    = from_addr
    msg["To"]      = to
    msg["Subject"] = subject
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, to, msg.as_string())


# =============================================================================
# ROUTE REGISTRATION  — call register_routes(app) once in app.py
# =============================================================================

def register_routes(app) -> None:
    """
    Register all Auth & Org v7 endpoints with the Flask app.

    Add to app.py after existing imports:

        from auth_org_backend import register_routes, audit, require_superadmin
        register_routes(app)
    """
    from auth import require_auth, require_admin

    # ── 1. Password reset ──────────────────────────────────────────────────

    @app.route("/api/auth/reset-request", methods=["POST"])
    def reset_request():
        d     = request.get_json(force=True) or {}
        email = (d.get("email") or "").strip()
        if not email:
            return jsonify({"error": "email is required"}), 400
        return jsonify(request_password_reset(email))

    @app.route("/api/auth/reset-verify", methods=["POST"])
    def reset_verify():
        d = request.get_json(force=True) or {}
        return jsonify(verify_reset_token((d.get("token") or "").strip()))

    @app.route("/api/auth/reset-confirm", methods=["POST"])
    def reset_confirm():
        d = request.get_json(force=True) or {}
        return jsonify(confirm_reset(
            (d.get("token") or "").strip(),
            d.get("new_password") or "",
        ))

    # ── 2. Invites ────────────────────────────────────────────────────────

    @app.route("/api/auth/invite", methods=["POST"])
    @require_admin
    def invite_user():
        d     = request.get_json(force=True) or {}
        email = (d.get("email") or "").strip()
        role  = (d.get("role")  or "analyst").strip()
        if not email:
            return jsonify({"error": "email is required"}), 400
        return jsonify(create_invite(email, role, g.org, g.user))

    @app.route("/api/auth/invite/<token>", methods=["GET"])
    def invite_info(token):
        return jsonify(get_invite(token))

    @app.route("/api/auth/invite/<token>/accept", methods=["POST"])
    def invite_accept(token):
        d        = request.get_json(force=True) or {}
        username = (d.get("username") or "").strip()
        password = d.get("password") or ""
        if not username or not password:
            return jsonify({"error": "username and password are required"}), 400
        return jsonify(accept_invite(token, username, password))

    # ── 4. Org settings ───────────────────────────────────────────────────

    @app.route("/api/org/settings", methods=["GET"])
    @require_auth
    def org_settings_get():
        return jsonify(load_org_settings(g.org))

    @app.route("/api/org/settings", methods=["POST"])
    @require_admin
    def org_settings_post():
        d = request.get_json(force=True) or {}
        save_org_settings(g.org, d)
        audit(g.user, g.org, "org_settings_update",
              f"currency={d.get('currency','')}, sector={d.get('sector','')}")
        return jsonify({"ok": True})

    # ── 5. Super-admin ────────────────────────────────────────────────────

    @app.route("/api/superadmin/orgs", methods=["GET"])
    @require_superadmin
    def superadmin_orgs():
        return jsonify({"orgs": list_all_orgs()})

    @app.route("/api/superadmin/orgs", methods=["POST"])
    @require_superadmin
    def superadmin_create_org():
        d    = request.get_json(force=True) or {}
        name = (d.get("name") or "").strip()
        res  = create_org(name)
        if "ok" in res:
            audit(g.user, g.org, "superadmin_create_org", name)
        return jsonify(res)

    @app.route("/api/superadmin/orgs/<path:org>/users", methods=["GET"])
    @require_superadmin
    def superadmin_org_users(org):
        return jsonify({"users": list_users_in_org_all(org)})

    @app.route("/api/superadmin/orgs/<path:org>/users/<username>/role", methods=["POST"])
    @require_superadmin
    def superadmin_set_role(org, username):
        d    = request.get_json(force=True) or {}
        role = (d.get("role") or "").strip()
        res  = set_user_role(username, role)
        if "ok" in res:
            audit(g.user, g.org, "role_change", f"{username}→{role} ({org})")
        return jsonify(res)

    # ── 6. Google SSO ─────────────────────────────────────────────────────

    google = _init_google_oauth(app)
    if google:
        @app.route("/api/auth/google")
        def google_login():
            redirect_uri = url_for("google_callback", _external=True)
            return google.authorize_redirect(redirect_uri)

        @app.route("/api/auth/google/callback")
        def google_callback():
            return _google_callback_logic(google)

    # ── 7. Audit log ──────────────────────────────────────────────────────

    @app.route("/api/audit")
    @require_admin
    def audit_log():
        page          = int(request.args.get("page",   1))
        limit         = min(int(request.args.get("limit", 50)), 200)
        user_filter   = request.args.get("user",   "").strip()
        action_filter = request.args.get("action", "").strip()
        return jsonify(_read_audit(g.org, page, limit, user_filter, action_filter))

    # ── Accept-invite SPA redirect ────────────────────────────────────────

    @app.route("/accept-invite")
    def accept_invite_page():
        token = request.args.get("token", "")
        return redirect(f"/?accept_invite=1&token={token}")


# =============================================================================
# AUDIT CALL EXAMPLES — copy these into app.py
# =============================================================================
#
# from auth_org_backend import audit, register_routes
# register_routes(app)
#
# In your login route:
#   result = login_user(username, password)
#   if result.get("ok"):
#       audit(username, result["org"], "login")
#
# In auth_logout():
#   audit(g.user, g.org, "logout")
#
# In analyze():
#   result = _analyze_and_store(project, g.org)
#   audit(g.user, g.org, "analyze", project.name)
#
# In bulk_import():
#   audit(g.user, g.org, "bulk_import", f"{len(results)} projects from {f.filename}")
#
# In download_pdf():
#   audit(g.user, g.org, "pdf_export", slug)
#
# In download_xlsx():
#   audit(g.user, g.org, "xlsx_export", slug)
