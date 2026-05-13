"""
Simple user-auth + per-org history layer for Budget Integrity Monitor.

Storage: JSON files under  reports/auth/
  - users.json   : {username: {password_hash, org, role}}
  - sessions.json: {token: {username, org, expires_iso}}

No external deps beyond the stdlib.  Password hashed with sha-256 + salt.
Token is a 32-byte random hex string stored server-side (not JWT).
"""

import hashlib
import json
import os
import secrets
import datetime
from pathlib import Path
from functools import wraps
from flask import request, jsonify, g

AUTH_DIR = Path("reports/auth")
AUTH_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE    = AUTH_DIR / "users.json"
SESSIONS_FILE = AUTH_DIR / "sessions.json"
SESSION_TTL_H = 24   # hours

# ── helpers ───────────────────────────────────────────────────────────────────

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

def _expires() -> str:
    return (_now() + datetime.timedelta(hours=SESSION_TTL_H)).isoformat()

# ── user management ───────────────────────────────────────────────────────────

def register_user(username: str, password: str, org: str, role: str = "analyst") -> dict:
    users = _load(USERS_FILE)
    if username in users:
        return {"error": "Username already exists"}
    if len(password) < 6:
        return {"error": "Password must be at least 6 characters"}
    salt = secrets.token_hex(8)
    users[username] = {
        "password_hash": _hash(password, salt),
        "salt": salt,
        "org": org.strip(),
        "role": role,
        "created": _now().isoformat(),
    }
    _save(USERS_FILE, users)
    return {"ok": True, "username": username, "org": org}

def login_user(username: str, password: str) -> dict:
    users = _load(USERS_FILE)
    user  = users.get(username)
    if not user:
        return {"error": "Invalid credentials"}
    if _hash(password, user["salt"]) != user["password_hash"]:
        return {"error": "Invalid credentials"}
    # Create session
    token    = secrets.token_hex(32)
    sessions = _load(SESSIONS_FILE)
    sessions[token] = {
        "username": username,
        "org": user["org"],
        "role": user["role"],
        "expires": _expires(),
    }
    _save(SESSIONS_FILE, sessions)
    return {"ok": True, "token": token, "username": username, "org": user["org"], "role": user["role"]}

def logout_user(token: str) -> None:
    sessions = _load(SESSIONS_FILE)
    sessions.pop(token, None)
    _save(SESSIONS_FILE, sessions)

def get_session(token: str) -> dict | None:
    sessions = _load(SESSIONS_FILE)
    sess = sessions.get(token)
    if not sess:
        return None
    if _now().isoformat() > sess["expires"]:
        sessions.pop(token)
        _save(SESSIONS_FILE, sessions)
        return None
    return sess

def list_users_in_org(org: str) -> list[dict]:
    users = _load(USERS_FILE)
    return [
        {"username": u, "role": d["role"], "created": d["created"]}
        for u, d in users.items()
        if d["org"] == org
    ]

def seed_demo_users() -> None:
    """Create demo accounts if no users exist yet."""
    users = _load(USERS_FILE)
    if users:
        return
    register_user("admin",   "admin123",   "PIBD",   "admin")
    register_user("analyst", "analyst123", "PIBD",   "analyst")
    register_user("viewer",  "viewer123",  "UNDP",   "viewer")

# ── Flask helpers ─────────────────────────────────────────────────────────────

def _get_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("bim_token")

def require_auth(f):
    """Decorator: sets g.user / g.org / g.role or returns 401."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = _get_token()
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        sess = get_session(token)
        if not sess:
            return jsonify({"error": "Session expired or invalid"}), 401
        g.user = sess["username"]
        g.org  = sess["org"]
        g.role = sess["role"]
        return f(*args, **kwargs)
    return wrapped

def require_admin(f):
    """Decorator: like require_auth but also checks role == admin."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = _get_token()
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        sess = get_session(token)
        if not sess:
            return jsonify({"error": "Session expired or invalid"}), 401
        if sess.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        g.user = sess["username"]
        g.org  = sess["org"]
        g.role = sess["role"]
        return f(*args, **kwargs)
    return wrapped
