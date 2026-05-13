"""
Alerting System — Budget Integrity Monitor
Sends CRITICAL risk alerts via Email (SMTP) and/or Telegram Bot API.

Settings stored at: data/alert_settings.json
"""

import json
import smtplib
import ssl
import urllib.request
import urllib.parse
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

ALERT_SETTINGS_PATH = Path("data/alert_settings.json")


# ── Default settings schema ───────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "username": "",
        "password": "",
        "from_addr": "",
        "to_addrs": []
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": ""
    },
    "notify_on": ["Critical"],          # "Critical" | "High" | "Medium"
    "last_updated": ""
}


# ── Persistence ───────────────────────────────────────────────────────────────

def load_settings() -> dict:
    """Load alert settings from disk, falling back to defaults."""
    if ALERT_SETTINGS_PATH.exists():
        try:
            stored = json.loads(ALERT_SETTINGS_PATH.read_text(encoding="utf-8"))
            # Merge with defaults so new keys always exist
            merged = json.loads(json.dumps(DEFAULT_SETTINGS))
            merged["email"].update(stored.get("email", {}))
            merged["telegram"].update(stored.get("telegram", {}))
            merged["notify_on"] = stored.get("notify_on", merged["notify_on"])
            return merged
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings: dict) -> None:
    """Persist alert settings to disk."""
    ALERT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings["last_updated"] = datetime.utcnow().isoformat()
    ALERT_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Email ─────────────────────────────────────────────────────────────────────

def _build_email_body(project_name: str, country: str, score: int,
                       risk_level: str, findings: list[str]) -> str:
    findings_text = "\n".join(f"  • {f}" for f in findings[:6])
    return f"""\
╔══════════════════════════════════════════════════════════╗
  🔴  {risk_level.upper()} RISK ALERT — Budget Integrity Monitor
╚══════════════════════════════════════════════════════════╝

Project  : {project_name}
Country  : {country}
Score    : {score}/100
Risk     : {risk_level} ⚠

Key Findings:
{findings_text}

──────────────────────────────────────────────────────────
Please review this project immediately in the Budget
Integrity Monitor dashboard.

Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
"""


def send_email_alert(cfg: dict, project_name: str, country: str,
                      score: int, risk_level: str, findings: list[str]) -> None:
    """Send alert email via SMTP with STARTTLS."""
    if not cfg.get("enabled"):
        return
    required = ["smtp_host", "username", "password", "from_addr"]
    if not all(cfg.get(k) for k in required):
        raise ValueError("Email config incomplete — check smtp_host, username, password, from_addr")
    if not cfg.get("to_addrs"):
        raise ValueError("No recipient addresses configured")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔴 [{risk_level}] Budget Alert: {project_name} ({country})"
    msg["From"]    = cfg["from_addr"]
    msg["To"]      = ", ".join(cfg["to_addrs"])

    body = _build_email_body(project_name, country, score, risk_level, findings)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587))) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(cfg["username"], cfg["password"])
        server.send_message(msg)


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram_alert(cfg: dict, project_name: str, country: str,
                         score: int, risk_level: str, findings: list[str]) -> None:
    """Send alert via Telegram Bot API."""
    if not cfg.get("enabled"):
        return
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        raise ValueError("Telegram config incomplete — need bot_token and chat_id")

    emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡"}.get(risk_level, "⚠")
    top_findings = "\n".join(f"• {f}" for f in findings[:4])

    text = (
        f"{emoji} *{risk_level.upper()} BUDGET ALERT*\n\n"
        f"🏗 *Project:* {project_name}\n"
        f"🌍 *Country:* {country}\n"
        f"📊 *Integrity Score:* {score}/100\n\n"
        f"*Key Findings:*\n{top_findings}\n\n"
        f"_LDC Budget Integrity Monitor — PIBD_"
    )

    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id":    cfg["chat_id"],
        "text":       text,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result}")


# ── Test helpers ──────────────────────────────────────────────────────────────

def send_test_alert(channel: str) -> dict:
    """
    Send a test alert on the given channel ('email' or 'telegram').
    Returns {'ok': True} or {'ok': False, 'error': '...'}
    """
    settings = load_settings()
    cfg = settings.get(channel, {})
    dummy_findings = [
        "Consultancy spend 38% of total budget (threshold: 20%)",
        "Tied-aid procurement detected — donor-mandated vendors only",
        "Cost per beneficiary USD 420 — 2.1× regional benchmark",
    ]
    try:
        if channel == "email":
            send_email_alert(cfg, "TEST PROJECT", "Bangladesh",
                             12, "Critical", dummy_findings)
        elif channel == "telegram":
            send_telegram_alert(cfg, "TEST PROJECT", "Bangladesh",
                                12, "Critical", dummy_findings)
        else:
            return {"ok": False, "error": f"Unknown channel: {channel}"}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Main dispatcher ───────────────────────────────────────────────────────────

def fire_alerts_if_needed(report_dict: dict) -> list[str]:
    """
    Check if the report risk level warrants an alert.
    Returns list of error strings (empty = all OK / no alert needed).
    """
    settings   = load_settings()
    risk_level = report_dict.get("overall_risk", "")
    notify_on  = settings.get("notify_on", ["Critical"])

    if risk_level not in notify_on:
        return []

    project_name = report_dict.get("project_name", "Unknown Project")
    country      = report_dict.get("country", "Unknown")
    score        = report_dict.get("overall_score", 0)

    # Gather findings from all sub-reports
    findings: list[str] = []
    for section in ("leakage", "impact", "sovereign"):
        findings.extend(report_dict.get(section, {}).get("findings", []))

    errors: list[str] = []

    # Email
    try:
        send_email_alert(
            settings["email"], project_name, country, score, risk_level, findings
        )
    except Exception as exc:
        errors.append(f"email: {exc}")

    # Telegram
    try:
        send_telegram_alert(
            settings["telegram"], project_name, country, score, risk_level, findings
        )
    except Exception as exc:
        errors.append(f"telegram: {exc}")

    return errors
