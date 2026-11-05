"""
Budget Integrity Monitor — Flask Web API & UI  (v5 — org auth + trend dashboard)

Endpoints:
  POST /api/auth/register          → register user + org
  POST /api/auth/login             → obtain session token
  POST /api/auth/logout            → invalidate token
  GET  /api/auth/me                → current session info
  GET  /api/auth/users             → list users in org (admin only)

  GET  /                           → web dashboard (HTML)
  POST /api/analyze                → JSON project → IntegrityReport JSON  [auth]
  POST /api/bulk                   → multipart CSV file → reports          [auth]
  GET  /api/history/<slug>         → historical snapshots for project      [auth]
  GET  /api/compare/<slug>         → diff two latest snapshots             [auth]
  GET  /api/report/<slug>/pdf      → download PDF of latest report         [auth]
  GET  /api/report/<slug>/xlsx     → download xlsx of latest report        [auth]
  GET  /api/template/csv           → download blank CSV template
  GET  /api/projects               → list projects with history            [auth]
  GET  /api/map                    → map markers                           [auth]
  GET  /api/risk-trends            → country risk over time                [auth]
  GET  /api/donor-comparison/xlsx  → donor comparison xlsx                 [auth]
"""

import asyncio
import io
import json
import os
import sys
import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from flask import Flask, request, jsonify, send_file, render_template_string, g

from core.models import BudgetProject
from core.orchestrator import BudgetOrchestrator
from config.settings import Settings
from tools.csv_import import parse_csv, generate_template_csv
from tools.org_history import (
    save_report, load_history, compare_latest_two, compare_any_two,
    list_projects, get_risk_trends,
    load_snapshot_meta, save_snapshot_meta,
)
from tools.pdf_export import export_pdf
from tools.excel_export import export_to_bytes, export_single_report
from tools.alerting import (
    fire_alerts_if_needed, load_settings as load_alert_settings,
    save_settings as save_alert_settings, send_test_alert,
)
from data.country_coords import get_coords
from auth import (
    register_user, login_user, logout_user, get_session,
    list_users_in_org, seed_demo_users,
    require_auth, require_admin, _get_token,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

settings    = Settings()
orchestrator = BudgetOrchestrator(settings)

seed_demo_users()   # creates demo accounts if none exist

# ── helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _analyze_and_store(project: BudgetProject, org: str) -> dict:
    report = _run(orchestrator.run_full_analysis(project))
    save_report(report, org)
    result = json.loads(report.to_json())
    result["donor"]           = project.donor
    result["total_budget_usd"] = project.total_budget_usd
    result["sector"]          = project.sector
    result["line_items"] = [
        {"name": li.name, "amount_usd": li.amount_usd,
         "category": li.category, "vendor_origin": li.vendor_origin}
        for li in project.line_items
    ]
    try:
        fire_alerts_if_needed(result)
    except Exception as exc:
        app.logger.warning("Alert dispatch failed: %s", exc)
    return result


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    d = request.get_json(force=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    org      = (d.get("org") or "").strip()
    if not username or not password or not org:
        return jsonify({"error": "username, password, and org are required"}), 400
    res = register_user(username, password, org)
    if "error" in res:
        return jsonify(res), 409
    return jsonify(res)


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    d = request.get_json(force=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    res = login_user(username, password)
    if "error" in res:
        return jsonify(res), 401
    return jsonify(res)


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = _get_token()
    if token:
        logout_user(token)
    return jsonify({"ok": True})


@app.route("/api/auth/me")
@require_auth
def auth_me():
    return jsonify({"username": g.user, "org": g.org, "role": g.role})


@app.route("/api/auth/users")
@require_admin
def auth_users():
    return jsonify({"users": list_users_in_org(g.org)})


# ── Main routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/analyze", methods=["POST"])
@require_auth
def analyze():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Empty body"}), 400
    try:
        project = BudgetProject.from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": f"Invalid project data: {e}"}), 422
    try:
        result = _analyze_and_store(project, g.org)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bulk", methods=["POST"])
@require_auth
def bulk_import():
    if "file" not in request.files:
        return jsonify({"error": "No file field in request (key: 'file')"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".csv"):
        return jsonify({"error": "Expected a .csv file"}), 400
    text = f.stream.read().decode("utf-8-sig")
    try:
        projects = parse_csv(io.StringIO(text))
    except Exception as e:
        return jsonify({"error": f"CSV parse error: {e}"}), 422
    if not projects:
        return jsonify({"error": "No valid projects found in CSV"}), 422
    results, errors = [], []
    for project in projects:
        try:
            r = _analyze_and_store(project, g.org)
            results.append(r)
        except Exception as e:
            errors.append({"project": project.name, "error": str(e)})
    return jsonify({"analyzed": len(results), "errors": errors, "reports": results})


@app.route("/api/history/<slug>")
@require_auth
def history(slug: str):
    snaps = load_history(slug, g.org)
    if not snaps:
        return jsonify({"error": "No history found"}), 404
    return jsonify({"project": slug, "count": len(snaps), "snapshots": snaps})


@app.route("/api/compare/<slug>")
@require_auth
def compare(slug: str):
    result = compare_latest_two(slug, g.org)
    if result is None:
        return jsonify({"error": "Need at least 2 snapshots to compare"}), 404
    return jsonify(result.to_dict())


@app.route("/api/report/<slug>/pdf")
@require_auth
def download_pdf(slug: str):
    snaps = load_history(slug, g.org)
    if not snaps:
        return jsonify({"error": "No report found"}), 404
    latest = snaps[-1]
    from core.models import (
        IntegrityReport, LeakageResult, ImpactResult,
        SovereignAlignmentResult, RiskLevel,
    )
    def _lvl(v):
        return RiskLevel(v) if v else RiskLevel.LOW
    lk = latest["leakage"]
    im = latest["impact"]
    sv = latest["sovereign"]
    report = IntegrityReport(
        project_name=latest["project_name"],
        country=latest["country"],
        generated_at=latest["generated_at"],
        overall_risk=_lvl(latest["overall_risk"]),
        overall_score=latest["overall_score"],
        leakage=LeakageResult(
            risk_level=_lvl(lk["risk_level"]),
            consultancy_pct=lk["consultancy_pct"],
            admin_overhead_pct=lk["admin_overhead_pct"],
            findings=lk["findings"],
            recommendation=lk["recommendation"],
            integrity_score=lk["integrity_score"],
        ),
        impact=ImpactResult(
            risk_level=_lvl(im["risk_level"]),
            cost_per_beneficiary_usd=im.get("cost_per_beneficiary_usd"),
            hdx_alignment_score=im.get("hdx_alignment_score"),
            findings=im["findings"],
            recommendation=im["recommendation"],
            integrity_score=im["integrity_score"],
        ),
        sovereign=SovereignAlignmentResult(
            risk_level=_lvl(sv["risk_level"]),
            tied_aid_detected=sv["tied_aid_detected"],
            conditionality_risk=sv["conditionality_risk"],
            national_law_override_detected=sv["national_law_override_detected"],
            findings=sv["findings"],
            recommendation=sv["recommendation"],
            integrity_score=sv["integrity_score"],
        ),
        civil_society_summary=latest.get("civil_society_summary", ""),
    )
    buf = io.BytesIO(export_pdf(report))
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{slug}_report.pdf")


@app.route("/api/report/<slug>/xlsx")
@require_auth
def download_xlsx(slug: str):
    snaps = load_history(slug, g.org)
    if not snaps:
        return jsonify({"error": "No report found"}), 404
    xlsx_bytes = export_single_report(snaps[-1])
    buf = io.BytesIO(xlsx_bytes)
    buf.seek(0)
    return send_file(buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"{slug}_report.xlsx")


@app.route("/api/template/csv")
def template_csv():
    buf = io.BytesIO(generate_template_csv().encode())
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name="budget_template.csv")


@app.route("/api/projects")
@require_auth
def projects():
    return jsonify({"projects": list_projects(g.org)})


@app.route("/api/map")
@require_auth
def map_data():
    import re
    slugs = list_projects(g.org)
    markers = []
    for slug in slugs:
        snaps = load_history(slug, g.org)
        if not snaps:
            continue
        latest = snaps[-1]
        coords = get_coords(latest.get("country", ""))
        if not coords:
            continue
        lat, lon = coords
        markers.append({
            "project_name": latest["project_name"],
            "country": latest["country"],
            "overall_risk": latest["overall_risk"],
            "overall_score": latest["overall_score"],
            "generated_at": latest["generated_at"][:10],
            "lat": lat, "lon": lon,
            "slug": slug,
            "donor": latest.get("donor", ""),
        })
    return jsonify({"markers": markers})


@app.route("/api/risk-trends")
@require_auth
def risk_trends():
    return jsonify(get_risk_trends(g.org))


@app.route("/api/donor-comparison/xlsx")
@require_auth
def donor_comparison_xlsx():
    slugs = list_projects(g.org)
    all_reports, history_data = [], {}
    for s in slugs:
        snaps = load_history(s, g.org)
        if snaps:
            all_reports.append(snaps[-1])
            history_data[s] = snaps
    if not all_reports:
        return jsonify({"error": "No projects analyzed yet"}), 404
    xlsx_bytes = export_to_bytes(all_reports, history_data=history_data, mode="comparison")
    buf = io.BytesIO(xlsx_bytes)
    buf.seek(0)
    return send_file(buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="donor_comparison_report.xlsx")


@app.route("/api/alerts/settings", methods=["GET"])
@require_auth
def get_alert_settings():
    s = load_alert_settings()
    masked = json.loads(json.dumps(s))
    if masked["email"].get("password"):
        masked["email"]["password"] = "••••••••"
    return jsonify(masked)


@app.route("/api/alerts/settings", methods=["POST"])
@require_auth
def update_alert_settings():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Empty body"}), 400
    existing = load_alert_settings()
    new_email = data.get("email", {})
    if new_email.get("password", "").startswith("•"):
        new_email["password"] = existing["email"].get("password", "")
    existing["email"].update(new_email)
    existing["telegram"].update(data.get("telegram", {}))
    if "notify_on" in data:
        existing["notify_on"] = data["notify_on"]
    save_alert_settings(existing)
    return jsonify({"ok": True})


@app.route("/api/alerts/test/<channel>", methods=["POST"])
@require_auth
def test_alert(channel: str):
    if channel not in ("email", "telegram"):
        return jsonify({"error": "Unknown channel"}), 400
    result = send_test_alert(channel)
    return jsonify(result)


@app.route("/api/compare/<slug>/any", methods=["POST"])
@require_auth
def compare_any(slug: str):
    """Compare any two snapshots by index."""
    d = request.get_json(force=True) or {}
    try:
        idx_a = int(d.get("idx_a", 0))
        idx_b = int(d.get("idx_b", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "idx_a and idx_b must be integers"}), 400
    result = compare_any_two(slug, g.org, idx_a, idx_b)
    if result is None:
        return jsonify({"error": "Not enough snapshots or invalid indices"}), 404
    return jsonify(result.to_dict())


@app.route("/api/history/<slug>/meta", methods=["GET"])
@require_auth
def get_snapshot_meta(slug: str):
    meta = load_snapshot_meta(slug, g.org)
    return jsonify({"meta": meta})


@app.route("/api/history/<slug>/meta", methods=["POST"])
@require_auth
def update_snapshot_meta(slug: str):
    d = request.get_json(force=True) or {}
    idx = str(d.get("idx", ""))
    tag = (d.get("tag") or "").strip()
    annotation = (d.get("annotation") or "").strip()
    if not idx:
        return jsonify({"error": "idx is required"}), 400
    meta = load_snapshot_meta(slug, g.org)
    meta[idx] = {"tag": tag, "annotation": annotation}
    save_snapshot_meta(slug, g.org, meta)
    return jsonify({"ok": True})


@app.route("/api/report/<slug>/docx")
@require_auth
def download_docx(slug: str):
    """Download Word (.docx) narrative report."""
    snaps = load_history(slug, g.org)
    if not snaps:
        return jsonify({"error": "No report found"}), 404
    latest = snaps[-1]
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from io import BytesIO

        doc = Document()
        doc.add_heading("Budget Integrity Report", 0)
        doc.add_paragraph(f"Project: {latest['project_name']}")
        doc.add_paragraph(f"Country: {latest['country']}")
        doc.add_paragraph(f"Generated: {latest['generated_at'][:10]}")
        doc.add_paragraph(f"Overall Risk: {latest['overall_risk']}  |  Score: {latest['overall_score']}/100")

        doc.add_heading("1. Leakage Detection", level=1)
        lk = latest["leakage"]
        doc.add_paragraph(f"Risk: {lk['risk_level']}  |  Score: {lk['integrity_score']}/100")
        doc.add_paragraph(f"Consultancy: {lk['consultancy_pct']}%  |  Admin Overhead: {lk['admin_overhead_pct']}%")
        for f in lk["findings"]:
            doc.add_paragraph(f"• {f}", style="List Bullet")
        doc.add_paragraph(f"Recommendation: {lk['recommendation']}")

        doc.add_heading("2. Impact-to-Citizen", level=1)
        im = latest["impact"]
        doc.add_paragraph(f"Risk: {im['risk_level']}  |  Score: {im['integrity_score']}/100")
        if im.get("cost_per_beneficiary_usd"):
            doc.add_paragraph(f"Cost per Beneficiary: USD {im['cost_per_beneficiary_usd']:,.0f}")
        for f in im["findings"]:
            doc.add_paragraph(f"• {f}", style="List Bullet")
        doc.add_paragraph(f"Recommendation: {im['recommendation']}")

        doc.add_heading("3. Sovereign Alignment", level=1)
        sv = latest["sovereign"]
        doc.add_paragraph(f"Risk: {sv['risk_level']}  |  Score: {sv['integrity_score']}/100")
        doc.add_paragraph(f"Tied Aid: {'Yes' if sv['tied_aid_detected'] else 'No'}  |  Conditionality: {'Yes' if sv['conditionality_risk'] else 'No'}")
        for f in sv["findings"]:
            doc.add_paragraph(f"• {f}", style="List Bullet")
        doc.add_paragraph(f"Recommendation: {sv['recommendation']}")

        doc.add_heading("Civil Society Summary", level=1)
        doc.add_paragraph(latest.get("civil_society_summary", ""))

        buf = BytesIO()
        doc.save(buf)
        buf.seek(0)
        return send_file(buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True, download_name=f"{slug}_report.docx")
    except ImportError:
        return jsonify({"error": "python-docx not installed"}), 500


@app.route("/api/report/<slug>/email", methods=["POST"])
@require_auth
def email_report(slug: str):
    """Email the latest report summary."""
    snaps = load_history(slug, g.org)
    if not snaps:
        return jsonify({"error": "No report found"}), 404
    latest = snaps[-1]
    d = request.get_json(force=True) or {}
    to_addr = (d.get("to") or "").strip()
    if not to_addr:
        return jsonify({"error": "to email is required"}), 400
    try:
        alert_settings = load_alert_settings()
        em = alert_settings.get("email", {})
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg["From"] = em.get("from_addr", "monitor@example.com")
        msg["To"] = to_addr
        msg["Subject"] = f"Budget Integrity Report — {latest['project_name']} ({latest['overall_risk']})"
        body = f"""Budget Integrity Report
{'='*50}
Project: {latest['project_name']}
Country: {latest['country']}
Generated: {latest['generated_at'][:10]}
Overall Risk: {latest['overall_risk']}
Overall Score: {latest['overall_score']}/100

Leakage Score: {latest['leakage']['integrity_score']}/100
Impact Score:  {latest['impact']['integrity_score']}/100
Sovereign Score: {latest['sovereign']['integrity_score']}/100

Civil Society Summary:
{latest.get('civil_society_summary', '')}
"""
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(em.get("smtp_host", "smtp.gmail.com"), int(em.get("smtp_port", 587))) as server:
            server.starttls()
            server.login(em.get("username", ""), em.get("password", ""))
            server.sendmail(msg["From"], to_addr, msg.as_string())
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/anomalies")
@require_auth
def get_anomalies():
    """Detect sudden score drops across all projects."""
    slugs = list_projects(g.org)
    anomalies = []
    for slug in slugs:
        snaps = load_history(slug, g.org)
        if len(snaps) < 2:
            continue
        for i in range(1, len(snaps)):
            prev = snaps[i-1]
            curr = snaps[i]
            delta = curr["overall_score"] - prev["overall_score"]
            if delta <= -10:  # 10+ point drop
                anomalies.append({
                    "project": curr["project_name"],
                    "slug": slug,
                    "country": curr["country"],
                    "date": curr["generated_at"][:10],
                    "prev_score": prev["overall_score"],
                    "curr_score": curr["overall_score"],
                    "delta": round(delta, 1),
                    "prev_risk": prev["overall_risk"],
                    "curr_risk": curr["overall_risk"],
                })
    anomalies.sort(key=lambda x: x["delta"])
    return jsonify({"anomalies": anomalies})


@app.route("/api/sector-benchmarks")
@require_auth
def sector_benchmarks():
    """Aggregate scores by sector for benchmarking."""
    slugs = list_projects(g.org)
    sectors: dict = {}
    for slug in slugs:
        snaps = load_history(slug, g.org)
        if not snaps:
            continue
        latest = snaps[-1]
        sector = latest.get("sector") or latest.get("project_name", "Unknown")
        # Try to extract sector from project data
        sectors.setdefault(sector, []).append({
            "project": latest["project_name"],
            "score": latest["overall_score"],
            "risk": latest["overall_risk"],
            "leakage": latest["leakage"]["integrity_score"],
            "impact": latest["impact"]["integrity_score"],
            "sovereign": latest["sovereign"]["integrity_score"],
        })
    result = {}
    for sector, entries in sectors.items():
        n = len(entries)
        result[sector] = {
            "count": n,
            "avg_score": round(sum(e["score"] for e in entries) / n, 1),
            "avg_leakage": round(sum(e["leakage"] for e in entries) / n, 1),
            "avg_impact": round(sum(e["impact"] for e in entries) / n, 1),
            "avg_sovereign": round(sum(e["sovereign"] for e in entries) / n, 1),
            "high_critical_count": sum(1 for e in entries if e["risk"] in ("High", "Critical")),
            "projects": [e["project"] for e in entries],
        }
    return jsonify({"sectors": result})


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>BIM · Budget Integrity Monitor</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --navy: #0D1B2A; --navy2: #152338; --gold: #C9A84C; --gold2: #e8c46a;
      --bg: #F0F2F5; --card: #fff; --border: #DDE1E7;
      --green: #1A9C55; --orange: #D4711A; --red: #B02828; --yellow: #C9900A;
      --green-bg: #EAF7F0; --red-bg: #FDF0F0;
      --font: 'IBM Plex Sans', system-ui, sans-serif;
      --mono: 'IBM Plex Mono', monospace;
      --radius: 8px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: var(--font); background: var(--bg); color: #1a1a2e; min-height: 100vh; }

    /* ── Login overlay ── */
    #login-overlay {
      position: fixed; inset: 0; background: var(--navy);
      display: flex; align-items: center; justify-content: center;
      z-index: 9999; transition: opacity .4s;
    }
    #login-overlay.hidden { opacity: 0; pointer-events: none; }
    .login-box {
      background: #fff; border-radius: 12px; padding: 2.5rem 2rem;
      width: 380px; box-shadow: 0 20px 60px rgba(0,0,0,.4);
    }
    .login-box h2 { font-size: 1.4rem; font-weight: 700; color: var(--navy); margin-bottom: .3rem; }
    .login-box p  { font-size: .83rem; color: #888; margin-bottom: 1.5rem; }
    .login-tabs   { display: flex; gap: .5rem; margin-bottom: 1.2rem; }
    .login-tab    { flex: 1; padding: .5rem; text-align: center; border: 2px solid var(--border);
                    border-radius: 6px; cursor: pointer; font-size: .85rem; font-weight: 600; }
    .login-tab.active { background: var(--navy); color: #fff; border-color: var(--navy); }
    .lf { display: flex; flex-direction: column; gap: .8rem; }
    .lf input { padding: .55rem .8rem; border: 1px solid var(--border); border-radius: 6px;
                font-size: .9rem; font-family: var(--font); outline: none; }
    .lf input:focus { border-color: var(--navy); }
    .lf button { padding: .7rem; background: var(--navy); color: #fff; border: none;
                 border-radius: 6px; font-size: .95rem; font-weight: 700; cursor: pointer; }
    .lf button:hover { background: var(--navy2); }
    #login-err { font-size: .82rem; color: var(--red); min-height: 1.2em; }
    .demo-hint { font-size: .75rem; color: #aaa; text-align: center; margin-top: .8rem;
                 background: #f9f9f9; padding: .6rem; border-radius: 6px; }
    .demo-hint b { color: #555; }

    /* ── App shell ── */
    header {
      background: var(--navy); color: #fff;
      padding: .85rem 2rem; display: flex; align-items: center; justify-content: space-between;
      border-bottom: 3px solid var(--gold);
    }
    .hdr-left { display: flex; align-items: center; gap: .8rem; }
    header h1 { font-size: 1.15rem; font-weight: 700; letter-spacing: -.02em; }
    header .sub { color: var(--gold); font-size: .78rem; font-family: var(--mono); }
    .org-pill {
      background: rgba(201,168,76,.15); border: 1px solid rgba(201,168,76,.4);
      color: var(--gold); font-size: .75rem; padding: .2rem .7rem; border-radius: 20px;
      font-family: var(--mono); cursor: default;
    }
    .hdr-right { display: flex; align-items: center; gap: .8rem; }
    .logout-btn {
      padding: .35rem .9rem; background: transparent; color: rgba(255,255,255,.6);
      border: 1px solid rgba(255,255,255,.2); border-radius: 6px;
      font-size: .8rem; cursor: pointer; transition: .2s;
    }
    .logout-btn:hover { color: #fff; border-color: rgba(255,255,255,.5); }
    .container { max-width: 1000px; margin: 1.5rem auto; padding: 0 1rem; }
    .tabs { display: flex; gap: .4rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
    .tab {
      padding: .45rem 1.1rem; border: 2px solid var(--border);
      border-radius: 6px; cursor: pointer; font-size: .85rem; font-weight: 600;
      background: var(--card); transition: all .15s; color: #555;
    }
    .tab.active { background: var(--navy); color: #fff; border-color: var(--navy); }
    .panel { display: none; }
    .panel.active { display: block; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
            padding: 1.4rem; margin-bottom: 1.2rem; }
    label { display: block; font-size: .8rem; font-weight: 700; margin-bottom: .3rem;
            color: #555; text-transform: uppercase; letter-spacing: .04em; }
    textarea, input[type=file], input[type=text] {
      width: 100%; padding: .6rem .8rem; border: 1px solid var(--border);
      border-radius: 6px; font-size: .88rem; font-family: var(--mono);
    }
    textarea { height: 300px; resize: vertical; }
    button {
      padding: .55rem 1.3rem; background: var(--navy); color: #fff;
      border: none; border-radius: 6px; cursor: pointer; font-size: .88rem;
      font-weight: 600; margin-top: .7rem; transition: background .15s;
    }
    button:hover { background: var(--navy2); }
    button.secondary { background: var(--gold); color: #fff; }
    button.secondary:hover { background: var(--gold2); }
    .result { margin-top: 1rem; }
    .risk-badge {
      display: inline-block; padding: .25rem .75rem;
      border-radius: 20px; font-weight: 700; font-size: .78rem; color: #fff;
      font-family: var(--mono); letter-spacing: .05em;
    }
    .risk-Low { background: var(--green); }
    .risk-Medium { background: var(--yellow); }
    .risk-High { background: var(--orange); }
    .risk-Critical { background: var(--red); }
    .score-bar { display: flex; align-items: center; gap: .7rem; margin: .4rem 0; }
    .score-bar .bar-track { flex: 1; height: 7px; background: #EEE; border-radius: 4px; }
    .score-bar .bar-fill { height: 100%; border-radius: 4px; transition: width .5s; }
    .findings li { margin: .4rem 0 .4rem 1.2rem; font-size: .85rem; line-height: 1.55; }
    .diff-table { width: 100%; border-collapse: collapse; font-size: .85rem; margin-top:.7rem; }
    .diff-table th { background: var(--navy); color: #fff; padding: .5rem; text-align: left; }
    .diff-table td { padding: .4rem .5rem; border-bottom: 1px solid var(--border); }
    .diff-table tr:nth-child(even) td { background: #F9FAFB; }
    .tag-improved { color: var(--green); font-weight: 700; }
    .tag-worsened { color: var(--red); font-weight: 700; }
    .tag-unchanged { color: #AAA; }
    .loading { color: #999; font-style: italic; padding: .5rem 0; font-size: .88rem; }
    .error { color: var(--red); background: var(--red-bg); padding: .75rem; border-radius: 6px; margin-top:.7rem; font-size: .88rem; }
    .success { color: var(--green); background: var(--green-bg); padding: .75rem; border-radius: 6px; margin-top:.7rem; font-size: .88rem; }
    .download-link { display: inline-block; margin-top: .7rem; color: var(--navy); text-decoration: underline; cursor: pointer; font-size: .88rem; }
    .tip { font-size: .78rem; color: #999; margin-top: .4rem; }
    #project-map { height: 460px; border-radius: var(--radius); border: 1px solid var(--border); }
    .map-legend { display: flex; gap: 1rem; margin-top: .7rem; flex-wrap: wrap; }
    .map-legend span { font-size: .8rem; display: flex; align-items: center; gap: .35rem; }
    .dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
    .alert-section { border: 1px solid var(--border); border-radius: 8px; padding: 1.1rem; margin-bottom: 1.1rem; }
    .alert-section h3 { font-size: .95rem; margin-bottom: .9rem; }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin-bottom: .8rem; }
    .form-row.full { grid-template-columns: 1fr; }
    .form-row input[type=text], .form-row input[type=password],
    .form-row input[type=number], .form-row input[type=email] {
      width: 100%; padding: .5rem .7rem; border: 1px solid var(--border);
      border-radius: 5px; font-size: .85rem; font-family: var(--font);
    }
    .toggle-row { display: flex; align-items: center; gap: .7rem; margin-bottom: .9rem; }
    .toggle-row label { margin: 0; font-size: .88rem; color: #333; }
    .checkbox-group { display: flex; gap: 1.2rem; margin-top: .3rem; }
    .checkbox-group label { font-weight: 400; color: #444; font-size: .85rem;
                            display: flex; align-items: center; gap: .3rem; }

    /* ── Trend dashboard ── */
    .trend-grid { display: grid; grid-template-columns: repeat(auto-fill,minmax(280px,1fr)); gap: 1rem; margin-top: 1rem; }
    .trend-card {
      background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 1.1rem; position: relative; overflow: hidden;
    }
    .trend-card::before {
      content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    }
    .trend-card.risk-Low::before    { background: var(--green); }
    .trend-card.risk-Medium::before { background: var(--yellow); }
    .trend-card.risk-High::before   { background: var(--orange); }
    .trend-card.risk-Critical::before { background: var(--red); }
    .trend-card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: .8rem; }
    .trend-country { font-size: .95rem; font-weight: 700; color: var(--navy); }
    .trend-delta { font-size: 1rem; font-weight: 700; font-family: var(--mono); }
    .trend-meta { font-size: .73rem; color: #999; margin-top: .2rem; font-family: var(--mono); }
    .trend-chart-wrap { height: 80px; }
    .trend-score-row { display: flex; justify-content: space-between; align-items: center; margin-top: .7rem; }
    .trend-score { font-size: 1.6rem; font-weight: 700; font-family: var(--mono); }
    .trend-sparkline-dots { display: flex; gap: .3rem; align-items: flex-end; height: 32px; }
    .sparkdot { width: 8px; border-radius: 2px; flex-shrink: 0; }
    .trend-summary-bar {
      background: var(--navy); color: #fff; border-radius: var(--radius);
      padding: 1rem 1.4rem; margin-bottom: 1rem; display: flex; gap: 2rem; align-items: center;
    }
    .trend-summary-bar .ts-item { text-align: center; }
    .trend-summary-bar .ts-num { font-size: 1.5rem; font-weight: 700; font-family: var(--mono); color: var(--gold); }
    .trend-summary-bar .ts-lbl { font-size: .72rem; color: rgba(255,255,255,.6); text-transform: uppercase; letter-spacing: .05em; }
    .trend-filter-row { display: flex; gap: .8rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }
    .trend-filter-row select {
      padding: .4rem .7rem; border: 1px solid var(--border); border-radius: 6px;
      font-size: .85rem; background: var(--card); cursor: pointer;
    }
    .trend-filter-row input[type=text] { width: 200px; font-family: var(--font); }

    /* Email modal */
    #email-modal { display: none; }
    #email-modal.show { display: flex !important; }

    /* Map filters */
    .map-filter-row { display: flex; gap: .7rem; margin-bottom: .8rem; flex-wrap: wrap; }
    .map-filter-row select { padding: .4rem .7rem; border: 1px solid var(--border); border-radius: 6px; font-size: .85rem; background: var(--card); }

    /* ── BIM visual refresh ── */
    :root {
      --navy: #102d3b; --navy2: #16475a; --gold: #d79a4d; --gold2: #eab66c;
      --bg: #eef3f1; --card: #fffefa; --border: #dbe6e1;
      --green: #2d8d68; --orange: #c9793d; --red: #c24d52; --yellow: #bf8d31;
      --green-bg: #eaf5ee; --red-bg: #fff1f0;
      --radius: 14px;
    }
    html { scroll-behavior: smooth; }
    body { background: radial-gradient(circle at 80% -10%, rgba(217,165,92,.13), transparent 30%), linear-gradient(135deg, #f4f7f5, #eaf1ee); color: #19313a; }
    body::before { content: ''; position: fixed; inset: 0; pointer-events: none; opacity: .3; background-image: linear-gradient(rgba(16,45,59,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(16,45,59,.025) 1px, transparent 1px); background-size: 32px 32px; }
    header { position: sticky; top: 0; z-index: 50; padding: .85rem clamp(1rem, 4vw, 3.5rem); background: linear-gradient(105deg, rgba(16,45,59,.98), rgba(20,73,84,.97)); border-bottom: 3px solid var(--gold); box-shadow: 0 10px 28px rgba(18,54,62,.18); backdrop-filter: blur(14px); }
    header h1 { display: flex; align-items: center; gap: .65rem; letter-spacing: -.035em; font-size: clamp(1rem, 1.8vw, 1.3rem); }
    .brand-mark, .login-mark { display: inline-grid; place-items: center; width: 2rem; height: 2rem; border-radius: 10px; color: var(--navy); background: linear-gradient(145deg, #efbd70, #c98237); box-shadow: 0 7px 14px rgba(0,0,0,.16); font-size: 1.2rem; }
    .sub { margin-left: 2.65rem; opacity: .78; letter-spacing: .02em; }
    .org-pill { background: rgba(220,169,87,.15); border-color: rgba(236,188,110,.45); }
    .container { max-width: 1440px; margin: 0 auto; padding: 1.7rem clamp(1rem, 4vw, 3.5rem) 4rem; }
    .tabs { position: sticky; top: 81px; z-index: 25; padding: .45rem; margin: 0 -0.45rem 1.5rem; border: 1px solid #d6e3de; border-radius: 14px; background: rgba(245,250,247,.88); box-shadow: 0 7px 21px rgba(25,63,54,.08); backdrop-filter: blur(14px); gap: .25rem; overflow-x: auto; flex-wrap: nowrap; }
    .tab { flex: 0 0 auto; border: 0; border-radius: 10px; padding: .62rem .95rem; color: #6c7f7c; background: transparent; font-size: .78rem; font-weight: 700; white-space: nowrap; }
    .tab:hover { color: var(--navy); background: rgba(255,255,255,.68); }
    .tab.active { color: #fff; background: var(--navy); border-color: var(--navy); box-shadow: 0 6px 14px rgba(16,45,59,.18); }
    .card, .trend-card, .alert-section { border: 1px solid #dbe7e2; border-radius: var(--radius); background: rgba(255,254,250,.92); box-shadow: 0 10px 28px rgba(25,63,54,.07); }
    .card { padding: clamp(1rem, 2.2vw, 1.65rem); }
    label { color: #617470; font-family: var(--mono); font-size: .7rem; letter-spacing: .08em; }
    textarea, input[type=file], input[type=text], .form-row input[type=text], .form-row input[type=password], .form-row input[type=number], .form-row input[type=email], .trend-filter-row select { border-color: #d5e2dd; border-radius: 10px; background: #fbfdfa; color: #19313a; transition: .18s ease; }
    textarea:focus, input:focus, select:focus { border-color: #79afa0; box-shadow: 0 0 0 3px rgba(76,143,121,.13); }
    button { border-radius: 10px; box-shadow: 0 4px 11px rgba(16,45,59,.08); }
    button:hover { transform: translateY(-1px); }
    button.secondary { background: var(--gold); }
    .risk-badge { box-shadow: 0 4px 10px rgba(16,45,59,.1); }
    .trend-summary-bar { border-radius: var(--radius); background: linear-gradient(105deg, #102d3b, #1c5960); box-shadow: 0 13px 26px rgba(16,45,59,.16); }
    .trend-summary-bar .ts-num { color: #f1c47b; }
    .trend-card { transition: .2s ease; }
    .trend-card:hover { transform: translateY(-3px); border-color: #a8c9bc; box-shadow: 0 15px 30px rgba(25,63,54,.12); }
    #project-map { min-height: 360px; height: min(54vh, 520px); border: 0; border-radius: 13px; box-shadow: inset 0 0 0 1px #d3e1dc, 0 8px 20px rgba(24,58,51,.08); }
    .map-legend { padding: .55rem .7rem; border-radius: 10px; background: rgba(255,254,250,.7); width: fit-content; }
    .diff-table { border: 1px solid #dce7e2; border-radius: 10px; overflow: hidden; }
    .diff-table th { background: var(--navy2); }
    .diff-table td { border-color: #e2ebe7; }
    .login-box { width: min(420px, calc(100vw - 2rem)); padding: 2.5rem 2.2rem; border: 1px solid rgba(215,154,77,.25); border-radius: 20px; background: rgba(255,254,250,.97); box-shadow: 0 25px 80px rgba(0,0,0,.3); }
    .login-box h2 { display: flex; align-items: center; gap: .65rem; color: var(--navy); font-size: 1.35rem; }
    .login-box p { line-height: 1.5; color: #73817d; }
    .login-tab { border: 1px solid #d8e4df; border-radius: 9px; color: #70807b; }
    .login-tab.active { background: var(--navy); border-color: var(--navy); box-shadow: 0 6px 13px rgba(16,45,59,.16); }
    .lf input { border-radius: 10px; padding: .72rem .85rem; border-color: #d5e2dd; background: #fbfdfa; }
    .lf button { padding: .78rem; border-radius: 10px; background: linear-gradient(135deg, #153f4d, #1b6770); }
    .demo-hint { color: #83908b; background: #f0f5f2; border: 1px solid #dfeae4; }
    .success, .error { border-radius: 10px; }
    @media (max-width: 780px) { header { padding: .75rem 1rem; } .hdr-left { min-width: 0; } header .sub { display: none; } .hdr-right { gap: .35rem; } .org-pill { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .container { padding-top: 1rem; } .tabs { top: 68px; } .form-row { grid-template-columns: 1fr; } .trend-summary-bar { gap: 1rem; overflow-x: auto; justify-content: flex-start; } .trend-summary-bar .ts-item { min-width: 92px; } }
    @media (max-width: 520px) { header h1 { font-size: 1rem; } .brand-mark { width: 1.75rem; height: 1.75rem; font-size: 1rem; } .hdr-right #user-label { display: none; } .logout-btn { padding: .35rem .55rem; font-size: .72rem; } .container { padding-left: .7rem; padding-right: .7rem; } .tabs { margin-left: -.2rem; margin-right: -.2rem; } .card { padding: .95rem; } .trend-grid { grid-template-columns: 1fr; } #project-map { height: 52vh; min-height: 300px; } .map-legend { gap: .65rem; } .map-legend span { font-size: .72rem; } }
  </style>
</head>
<body>

<!-- ── Login Overlay ── -->
<div id="login-overlay">
  <div class="login-box">
    <h2><span class="login-mark">◎</span> Budget Integrity Monitor</h2>
    <p>AI-assisted fiscal governance · Sign in to your organization workspace</p>
    <div class="login-tabs">
      <div class="login-tab active" onclick="switchAuthTab('login')">Sign In</div>
      <div class="login-tab" onclick="switchAuthTab('register')">Register</div>
    </div>
    <!-- Sign In -->
    <div id="login-form">
      <div class="lf">
        <input type="text"     id="l-user" placeholder="Username">
        <input type="password" id="l-pass" placeholder="Password">
        <div id="login-err"></div>
        <button onclick="doLogin()">Sign In →</button>
      </div>
    </div>
    <!-- Register -->
    <div id="register-form" style="display:none">
      <div class="lf">
        <input type="text"     id="r-user" placeholder="Username">
        <input type="password" id="r-pass" placeholder="Password (min 6 chars)">
        <input type="text"     id="r-org"  placeholder="Organization name">
        <div id="reg-err"></div>
        <button onclick="doRegister()">Create Account →</button>
      </div>
    </div>
    <div class="demo-hint">
      Demo: <b>admin / admin123</b> (PIBD) &nbsp;·&nbsp; <b>viewer / viewer123</b> (UNDP)
    </div>
  </div>
</div>

<!-- ── App Shell ── -->
<header>
  <div class="hdr-left">
    <div>
      <h1><span class="brand-mark">◎</span> Budget Integrity Monitor</h1>
      <div class="sub">Political Insights BD (PIBD) · AI-assisted fiscal governance workspace</div>
    </div>
  </div>
  <div class="hdr-right">
    <span class="org-pill" id="org-pill">—</span>
    <span style="color:rgba(255,255,255,.7);font-size:.82rem" id="user-label">—</span>
    <button class="logout-btn" onclick="doLogout()">Sign Out</button>
  </div>
</header>

<div class="container">
  <div class="tabs">
    <div class="tab active" onclick="switchTab('analyze')">Analyze Project</div>
    <div class="tab" onclick="switchTab('bulk')">Bulk CSV</div>
    <div class="tab" onclick="switchTab('history')">History & Compare</div>
    <div class="tab" onclick="switchTab('map')">🗺 Map</div>
    <div class="tab" onclick="switchTab('donors')">📊 Donors</div>
    <div class="tab" onclick="switchTab('trends')">📈 Risk Trends</div>
    <div class="tab" onclick="switchTab('benchmarks')">🏭 Sectors</div>
    <div class="tab" onclick="switchTab('anomalies')">⚠ Anomalies</div>
    <div class="tab" onclick="switchTab('alerts')">🔔 Alerts</div>
  </div>

  <!-- ANALYZE -->
  <div id="tab-analyze" class="panel active">
    <div class="card">
      <label>Project JSON</label>
      <textarea id="json-input" spellcheck="false">{
  "name": "Rural Road Network Phase II",
  "country": "Bangladesh",
  "total_budget_usd": 8500000,
  "donor": "Asian Development Bank",
  "sector": "Infrastructure",
  "procurement_method": "ICB - restricted to ADB member countries",
  "beneficiary_count": 120000,
  "national_law_reference": "Public Procurement Rules (PPR) 2008, Section 32",
  "context_notes": "ADB requires use of pre-approved vendor list for equipment supply.",
  "line_items": [
    {"name": "Consultancy - International Firm", "amount_usd": 1800000, "category": "consultancy", "vendor_origin": "foreign"},
    {"name": "Consultancy - Local Technical Advisor", "amount_usd": 700000, "category": "consultancy", "vendor_origin": "local"},
    {"name": "Civil Works - Road Construction", "amount_usd": 3200000, "category": "civil_works", "vendor_origin": "local"},
    {"name": "Equipment - ADB Approved Vendor", "amount_usd": 1200000, "category": "equipment", "vendor_origin": "donor_mandated"},
    {"name": "Project Management Unit", "amount_usd": 600000, "category": "project_management", "vendor_origin": "local"},
    {"name": "Monitoring & Evaluation", "amount_usd": 500000, "category": "administration", "vendor_origin": "foreign"},
    {"name": "Contingency", "amount_usd": 200000, "category": "contingency", "vendor_origin": "local"}
  ]
}</textarea>
      <button onclick="runAnalysis()">▶ Run Analysis</button>
      <div class="result" id="analyze-result"></div>
    </div>
  </div>

  <!-- BULK -->
  <div id="tab-bulk" class="panel">
    <div class="card">
      <label>Upload CSV File</label>
      <input type="file" id="csv-file" accept=".csv">
      <p class="tip">Multiple projects in one CSV. Each row = one line item; rows with the same project_name are grouped.</p>
      <button onclick="runBulk()">⬆ Upload & Analyze All</button>
      <a class="download-link" onclick="downloadTemplate()">⬇ Download CSV Template</a>
      <div class="result" id="bulk-result"></div>
    </div>
  </div>

  <!-- HISTORY -->
  <div id="tab-history" class="panel">
    <div class="card">
      <label>Project Slug</label>
      <input type="text" id="history-slug" placeholder="e.g. rural_road_network_phase_ii">
      <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem">
        <button onclick="loadHistory()">📋 Load History</button>
        <button class="secondary" onclick="loadCompare()">⚖ Compare Latest Two</button>
      </div>
      <div id="history-download-links" style="display:none;margin-top:.7rem;display:flex;gap:.8rem;flex-wrap:wrap">
        <a class="download-link" id="pdf-link" style="display:none"></a>
        <a class="download-link" id="xlsx-link" style="display:none"></a>
        <a class="download-link" id="docx-link" style="display:none"></a>
        <a class="download-link" id="email-btn" href="#" style="display:none" onclick="showEmailModal();return false">📧 Email Report</a>
      </div>
      <!-- Compare any two -->
      <div id="compare-any-section" style="display:none;margin-top:.8rem;padding:.8rem;background:#f9f9fb;border-radius:6px;border:1px solid var(--border)">
        <label style="margin-bottom:.4rem">Compare Any Two Snapshots (0 = oldest)</label>
        <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
          <input type="number" id="cmp-idx-a" value="0" min="0" style="width:70px;padding:.4rem;border:1px solid var(--border);border-radius:5px;font-size:.85rem">
          <span style="color:#999">vs</span>
          <input type="number" id="cmp-idx-b" value="1" min="0" style="width:70px;padding:.4rem;border:1px solid var(--border);border-radius:5px;font-size:.85rem">
          <button onclick="loadCompareAny()" style="margin:0">⚖ Compare</button>
        </div>
      </div>
      <div class="result" id="history-result"></div>
    </div>
    <div class="card">
      <label>All projects in your organization</label>
      <button onclick="listProjects()">🔍 List Projects</button>
      <div id="projects-list" class="result"></div>
    </div>
  </div>

  <!-- Email modal -->
  <div id="email-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:8000;align-items:center;justify-content:center">
    <div style="background:#fff;border-radius:10px;padding:1.5rem;width:360px;box-shadow:0 20px 60px rgba(0,0,0,.3)">
      <h3 style="margin-bottom:.8rem">📧 Email Report</h3>
      <label>Recipient Email</label>
      <input type="text" id="email-to-modal" placeholder="recipient@example.com" style="margin-bottom:.8rem">
      <div style="display:flex;gap:.5rem">
        <button onclick="sendEmailReport()">Send</button>
        <button onclick="document.getElementById('email-modal').style.display='none'" style="background:#888">Cancel</button>
      </div>
      <div id="email-modal-result" style="margin-top:.5rem;font-size:.82rem"></div>
    </div>
  </div>


  <!-- MAP -->
  <div id="tab-map" class="panel">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.7rem">
        <div>
          <h2 style="font-size:1rem;font-weight:700">Project Risk Map</h2>
          <p style="font-size:.78rem;color:#888;margin-top:.2rem">Your organization's projects by country & risk level</p>
        </div>
        <button onclick="loadMap()">🔄 Refresh</button>
      </div>
      <div class="map-filter-row">
        <select id="map-filter-risk" onchange="applyMapFilters()">
          <option value="">All Risk Levels</option>
          <option value="Low">🟢 Low</option>
          <option value="Medium">🟡 Medium</option>
          <option value="High">🟠 High</option>
          <option value="Critical">🔴 Critical</option>
        </select>
        <select id="map-filter-donor" onchange="applyMapFilters()">
          <option value="">All Donors</option>
        </select>
      </div>
      <div id="project-map"></div>
      <div class="map-legend">
        <span><span class="dot" style="background:var(--green)"></span> Low</span>
        <span><span class="dot" style="background:var(--yellow)"></span> Medium</span>
        <span><span class="dot" style="background:var(--orange)"></span> High</span>
        <span><span class="dot" style="background:var(--red)"></span> Critical</span>
      </div>
      <div id="map-status" class="result"></div>
    </div>
  </div>

  <!-- DONOR COMPARISON -->
  <div id="tab-donors" class="panel">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="font-size:1rem;font-weight:700">📊 Donor Risk Pattern Comparison</h2>
          <p style="font-size:.78rem;color:#888;margin-top:.2rem">Aggregate risk patterns across analyzed projects by donor</p>
        </div>
        <div style="display:flex;gap:.5rem">
          <button onclick="loadDonorComparison()">🔄 Refresh</button>
          <button class="secondary" onclick="window.location.href='/api/donor-comparison/xlsx'">⬇ .xlsx</button>
        </div>
      </div>
      <div id="donor-result" class="result"><p style="color:#999;font-size:.85rem">Click Refresh to load.</p></div>
    </div>
  </div>

  <!-- RISK TRENDS -->
  <div id="tab-trends" class="panel">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="font-size:1rem;font-weight:700">📈 Country Risk Trend Dashboard</h2>
          <p style="font-size:.78rem;color:#888;margin-top:.2rem">Aggregate integrity scores over time, per country — scoped to your org</p>
        </div>
        <button onclick="loadRiskTrends()">🔄 Refresh</button>
      </div>
      <div class="trend-filter-row">
        <select id="trend-lang" onchange="renderTrendUI()">
          <option value="en">English</option>
          <option value="fr">Français</option>
          <option value="ar">العربية</option>
        </select>
        <input type="text" id="trend-search" placeholder="Filter country…" oninput="renderTrendUI()" style="font-family:var(--font)">
        <select id="trend-sort" onchange="renderTrendUI()">
          <option value="score_asc">Score: Low → High</option>
          <option value="score_desc">Score: High → Low</option>
          <option value="country_az">Country A–Z</option>
          <option value="trend_worst">Trend: Worsening first</option>
        </select>
      </div>
      <div id="trend-summary-bar"></div>
      <div id="trend-result" class="result"><p style="color:#999;font-size:.85rem">Click Refresh to load.</p></div>
    </div>
  </div>

  <!-- SECTOR BENCHMARKS -->
  <div id="tab-benchmarks" class="panel">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="font-size:1rem;font-weight:700">🏭 Sector Benchmarking</h2>
          <p style="font-size:.78rem;color:#888;margin-top:.2rem">Compare average integrity scores across project sectors</p>
        </div>
        <button onclick="loadSectorBenchmarks()">🔄 Refresh</button>
      </div>
      <div id="benchmark-result" class="result"><p style="color:#999;font-size:.85rem">Click Refresh to load.</p></div>
    </div>
  </div>

  <!-- ANOMALIES -->
  <div id="tab-anomalies" class="panel">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="font-size:1rem;font-weight:700">⚠ Anomaly Detection</h2>
          <p style="font-size:.78rem;color:#888;margin-top:.2rem">Projects with sudden score drops (≥10 points) between snapshots</p>
        </div>
        <button onclick="loadAnomalies()">🔄 Refresh</button>
      </div>
      <div id="anomaly-result" class="result"><p style="color:#999;font-size:.85rem">Click Refresh to load.</p></div>
    </div>
  </div>

  <!-- ALERTS -->
  <div id="tab-alerts" class="panel">
    <div class="card">
      <h2 style="font-size:1rem;font-weight:700;margin-bottom:.4rem">🔔 Alert Configuration</h2>
      <p style="font-size:.78rem;color:#888;margin-bottom:1.2rem">Receive notifications when a project reaches a critical risk level.</p>
      <div class="alert-section">
        <h3>Notify When Risk Level Is</h3>
        <div class="checkbox-group">
          <label><input type="checkbox" id="notify-critical" value="Critical"> 🔴 Critical</label>
          <label><input type="checkbox" id="notify-high"     value="High">     🟠 High</label>
          <label><input type="checkbox" id="notify-medium"   value="Medium">   🟡 Medium</label>
        </div>
      </div>
      <div class="alert-section">
        <div class="toggle-row">
          <input type="checkbox" id="email-enabled">
          <label for="email-enabled"><b>📧 Email Alerts</b></label>
        </div>
        <div class="form-row">
          <div><label>SMTP Host</label><input type="text" id="email-smtp-host" placeholder="smtp.gmail.com"></div>
          <div><label>SMTP Port</label><input type="number" id="email-smtp-port" placeholder="587" value="587"></div>
        </div>
        <div class="form-row">
          <div><label>Username</label><input type="text" id="email-username" placeholder="you@gmail.com"></div>
          <div><label>Password / App Password</label><input type="password" id="email-password" placeholder="••••••••"></div>
        </div>
        <div class="form-row">
          <div><label>From</label><input type="email" id="email-from" placeholder="monitor@example.com"></div>
          <div><label>To (comma-separated)</label><input type="text" id="email-to" placeholder="a@x.com, b@x.com"></div>
        </div>
        <button class="secondary" onclick="testAlert('email')" style="margin-right:.5rem">📬 Send Test Email</button>
        <div id="email-test-result" style="display:inline-block;font-size:.82rem;margin-top:.4rem"></div>
      </div>
      <div class="alert-section">
        <div class="toggle-row">
          <input type="checkbox" id="tg-enabled">
          <label for="tg-enabled"><b>✈️ Telegram Alerts</b></label>
        </div>
        <div class="form-row">
          <div><label>Bot Token</label><input type="text" id="tg-token" placeholder="123456:ABCdef..."></div>
          <div><label>Chat ID</label><input type="text" id="tg-chat-id" placeholder="-100123456789"></div>
        </div>
        <button class="secondary" onclick="testAlert('telegram')" style="margin-right:.5rem">📩 Send Test Telegram</button>
        <div id="tg-test-result" style="display:inline-block;font-size:.82rem;margin-top:.4rem"></div>
      </div>
      <button onclick="saveAlertSettings()">💾 Save Settings</button>
      <div id="alert-save-result" class="result"></div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ── State ─────────────────────────────────────────────────────────────────────
let SESSION_TOKEN = localStorage.getItem('bim_token') || '';
let SESSION_USER  = localStorage.getItem('bim_user')  || '';
let SESSION_ORG   = localStorage.getItem('bim_org')   || '';
let SESSION_ROLE  = localStorage.getItem('bim_role')  || '';
let leafletMap    = null;
let _trendRaw     = null;   // raw data from /api/risk-trends
let _trendCharts  = {};     // chart instances keyed by country

const ALL_TABS = ['analyze','bulk','history','map','donors','trends','benchmarks','anomalies','alerts'];

// ── Auth helpers ──────────────────────────────────────────────────────────────
function authHeaders() {
  return SESSION_TOKEN ? { 'Authorization': 'Bearer ' + SESSION_TOKEN, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

async function apiFetch(url, opts = {}) {
  opts.headers = Object.assign(authHeaders(), opts.headers || {});
  const res = await fetch(url, opts);
  if (res.status === 401) { doLogout(); throw new Error('Session expired. Please log in again.'); }
  return res;
}

function setSession(data) {
  SESSION_TOKEN = data.token || '';
  SESSION_USER  = data.username || '';
  SESSION_ORG   = data.org  || '';
  SESSION_ROLE  = data.role || '';
  localStorage.setItem('bim_token', SESSION_TOKEN);
  localStorage.setItem('bim_user',  SESSION_USER);
  localStorage.setItem('bim_org',   SESSION_ORG);
  localStorage.setItem('bim_role',  SESSION_ROLE);
}

function clearSession() {
  SESSION_TOKEN = SESSION_USER = SESSION_ORG = SESSION_ROLE = '';
  localStorage.removeItem('bim_token');
  localStorage.removeItem('bim_user');
  localStorage.removeItem('bim_org');
  localStorage.removeItem('bim_role');
}

function updateHeaderUI() {
  document.getElementById('org-pill').textContent   = SESSION_ORG  || '—';
  document.getElementById('user-label').textContent = SESSION_USER ? '@' + SESSION_USER + ' (' + SESSION_ROLE + ')' : '—';
}

// ── Login / Register ──────────────────────────────────────────────────────────
function switchAuthTab(tab) {
  document.querySelectorAll('.login-tab').forEach((t,i) => t.classList.toggle('active', (i===0&&tab==='login')||(i===1&&tab==='register')));
  document.getElementById('login-form').style.display    = tab === 'login'    ? '' : 'none';
  document.getElementById('register-form').style.display = tab === 'register' ? '' : 'none';
}

async function doLogin() {
  const u = document.getElementById('l-user').value.trim();
  const p = document.getElementById('l-pass').value;
  const err = document.getElementById('login-err');
  err.textContent = '';
  try {
    const res  = await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u,password:p}) });
    const data = await res.json();
    if (data.error) { err.textContent = data.error; return; }
    setSession(data);
    updateHeaderUI();
    document.getElementById('login-overlay').classList.add('hidden');
  } catch(e) { err.textContent = e.message; }
}

async function doRegister() {
  const u = document.getElementById('r-user').value.trim();
  const p = document.getElementById('r-pass').value;
  const o = document.getElementById('r-org').value.trim();
  const err = document.getElementById('reg-err');
  err.textContent = '';
  try {
    const res  = await fetch('/api/auth/register', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u,password:p,org:o}) });
    const data = await res.json();
    if (data.error) { err.textContent = data.error; return; }
    // Auto-login after register
    const lr = await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u,password:p}) });
    const ld = await lr.json();
    if (ld.error) { err.textContent = ld.error; return; }
    setSession(ld);
    updateHeaderUI();
    document.getElementById('login-overlay').classList.add('hidden');
  } catch(e) { err.textContent = e.message; }
}

async function doLogout() {
  try { await fetch('/api/auth/logout', { method:'POST', headers: authHeaders() }); } catch(_) {}
  clearSession();
  updateHeaderUI();
  document.getElementById('login-overlay').classList.remove('hidden');
}

// ── Tab navigation ────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const idx = ALL_TABS.indexOf(name);
  document.querySelectorAll('.tab')[idx].classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
  if (name === 'map')         initMap();
  if (name === 'donors')      loadDonorComparison();
  if (name === 'trends')      loadRiskTrends();
  if (name === 'alerts')      loadAlertSettings();
  if (name === 'benchmarks')  loadSectorBenchmarks();
  if (name === 'anomalies')   loadAnomalies();
}

function riskColor(r) {
  return {Low:'#1A9C55',Medium:'#C9900A',High:'#D4711A',Critical:'#B02828'}[r]||'#999';
}

// ── Map ───────────────────────────────────────────────────────────────────────
function initMap() {
  if (!leafletMap) {
    leafletMap = L.map('project-map').setView([20, 75], 3);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>', maxZoom: 18,
    }).addTo(leafletMap);
  }
  loadMap();
}

let _mapMarkers = [];

async function loadMap() {
  if (!leafletMap) return;
  const statusEl = document.getElementById('map-status');
  statusEl.innerHTML = '<p class="loading">Loading project markers…</p>';
  try {
    const res  = await apiFetch('/api/map');
    const data = await res.json();
    _mapMarkers = data.markers || [];
    // Populate donor filter
    const donorSel = document.getElementById('map-filter-donor');
    if (donorSel) {
      const donors = [...new Set(_mapMarkers.map(m => m.donor).filter(Boolean))];
      donorSel.innerHTML = '<option value="">All Donors</option>' + donors.map(d=>`<option value="${d}">${d}</option>`).join('');
    }
    applyMapFilters();
  } catch(e) { statusEl.innerHTML = `<div class="error">${e.message}</div>`; }
}

function applyMapFilters() {
  if (!leafletMap) return;
  const riskF  = document.getElementById('map-filter-risk')?.value  || '';
  const donorF = document.getElementById('map-filter-donor')?.value || '';
  const statusEl = document.getElementById('map-status');
  leafletMap.eachLayer(layer => { if (layer instanceof L.CircleMarker) leafletMap.removeLayer(layer); });
  const filtered = _mapMarkers.filter(m =>
    (!riskF  || m.overall_risk === riskF) &&
    (!donorF || m.donor === donorF)
  );
  if (!filtered.length) {
    statusEl.innerHTML = '<p style="color:#999;font-size:.85rem">No projects match filters.</p>'; return;
  }
  filtered.forEach(m => {
    const color = riskColor(m.overall_risk);
    L.circleMarker([m.lat, m.lon], {
      radius: 10 + Math.max(0,(100-m.overall_score)/15),
      fillColor: color, color: '#fff', weight: 2, opacity: 1, fillOpacity: .85,
    }).addTo(leafletMap).bindPopup(`
      <div style="min-width:180px;font-family:system-ui">
        <b>${m.project_name}</b><br>
        <span style="color:#666;font-size:.8rem">${m.country}</span><br>
        ${m.donor ? `<span style="color:#888;font-size:.75rem">${m.donor}</span><br>` : ''}
        <span style="display:inline-block;margin:.4rem 0;padding:.2rem .6rem;background:${color};color:#fff;border-radius:12px;font-size:.78rem;font-weight:700">${m.overall_risk}</span>
        <br><b>Score:</b> ${m.overall_score}/100<br>
        <span style="font-size:.75rem;color:#999">${m.generated_at}</span><br>
        <a href="#" onclick="document.getElementById('history-slug').value='${m.slug}';switchTab('history');return false" style="font-size:.8rem">View history →</a>
      </div>`);
  });
  statusEl.innerHTML = `<p style="font-size:.8rem;color:#666">Showing <b>${filtered.length}</b> of <b>${_mapMarkers.length}</b> project(s)</p>`;
}

// ── Report renderer ───────────────────────────────────────────────────────────
function renderReport(r, container) {
  const bar = (label, score) => `
    <div class="score-bar">
      <span style="min-width:130px;font-size:.82rem;font-family:var(--mono)">${label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${score}%;background:${score>=70?'var(--green)':score>=50?'var(--orange)':'var(--red)'}"></div></div>
      <b style="font-family:var(--mono);font-size:.88rem">${score}</b>
    </div>`;
  const findings = arr => '<ul class="findings">'+arr.map(f=>`<li>${f}</li>`).join('')+'</ul>';
  container.innerHTML = `
    <div style="margin-bottom:.7rem">
      <span class="risk-badge risk-${r.overall_risk}">${r.overall_risk}</span>
      <b style="margin-left:1rem;font-size:1.05rem;font-family:var(--mono)">Score: ${r.overall_score}/100</b>
      <span style="color:#999;font-size:.78rem;margin-left:.8rem">${r.generated_at}</span>
    </div>
    ${bar('Overall',r.overall_score)}${bar('Leakage',r.leakage.integrity_score)}${bar('Impact',r.impact.integrity_score)}${bar('Sovereign',r.sovereign.integrity_score)}
    <hr style="margin:.9rem 0;border-color:var(--border)">
    <h3 style="font-size:.9rem;margin-bottom:.3rem">🔍 Leakage Detection <span class="risk-badge risk-${r.leakage.risk_level}" style="font-size:.72rem">${r.leakage.risk_level}</span></h3>
    <p style="font-size:.82rem;font-family:var(--mono)">Consultancy: <b>${r.leakage.consultancy_pct}%</b> · Admin: <b>${r.leakage.admin_overhead_pct}%</b></p>
    ${findings(r.leakage.findings)}
    <p style="font-size:.82rem;color:#555"><b>→</b> ${r.leakage.recommendation}</p>
    <h3 style="font-size:.9rem;margin:.9rem 0 .3rem">👥 Impact-to-Citizen <span class="risk-badge risk-${r.impact.risk_level}" style="font-size:.72rem">${r.impact.risk_level}</span></h3>
    ${r.impact.cost_per_beneficiary_usd?`<p style="font-size:.82rem;font-family:var(--mono)">Cost/beneficiary: <b>USD ${r.impact.cost_per_beneficiary_usd.toLocaleString()}</b></p>`:''}
    ${findings(r.impact.findings)}
    <p style="font-size:.82rem;color:#555"><b>→</b> ${r.impact.recommendation}</p>
    <h3 style="font-size:.9rem;margin:.9rem 0 .3rem">🏛 Sovereign Alignment <span class="risk-badge risk-${r.sovereign.risk_level}" style="font-size:.72rem">${r.sovereign.risk_level}</span></h3>
    <p style="font-size:.82rem;font-family:var(--mono)">Tied Aid: <b>${r.sovereign.tied_aid_detected?'⚠ Yes':'✓ No'}</b> · Conditionality: <b>${r.sovereign.conditionality_risk?'⚠ Yes':'✓ No'}</b></p>
    ${findings(r.sovereign.findings)}
    <p style="font-size:.82rem;color:#555"><b>→</b> ${r.sovereign.recommendation}</p>
    <hr style="margin:.9rem 0;border-color:var(--border)">
    <h3 style="font-size:.9rem;margin-bottom:.3rem">📢 Civil Society Summary</h3>
    <p style="font-size:.88rem;line-height:1.6">${r.civil_society_summary}</p>`;
}

// ── Analyze ───────────────────────────────────────────────────────────────────
async function runAnalysis() {
  const out = document.getElementById('analyze-result');
  out.innerHTML = '<p class="loading">Running AI integrity analysis…</p>';
  try {
    const body = JSON.parse(document.getElementById('json-input').value);
    const res  = await apiFetch('/api/analyze', { method:'POST', body: JSON.stringify(body) });
    const data = await res.json();
    if (data.error) { out.innerHTML = `<div class="error">${data.error}</div>`; return; }
    renderReport(data, out);
  } catch(e) { out.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function runBulk() {
  const f = document.getElementById('csv-file').files[0];
  const out = document.getElementById('bulk-result');
  if (!f) { out.innerHTML='<div class="error">Please select a CSV file.</div>'; return; }
  out.innerHTML = '<p class="loading">Uploading and analyzing…</p>';
  const fd = new FormData(); fd.append('file', f);
  try {
    const res  = await apiFetch('/api/bulk', { method:'POST', body: fd, headers: { 'Authorization': 'Bearer ' + SESSION_TOKEN } });
    const data = await res.json();
    if (data.error) { out.innerHTML = `<div class="error">${data.error}</div>`; return; }
    let html = `<p><b>Analyzed: ${data.analyzed}</b>${data.errors.length?` | Errors: ${data.errors.length}`:''}</p>`;
    data.reports.forEach(r => {
      html += `<hr style="margin:1rem 0"><h3 style="font-size:.95rem">${r.project_name} (${r.country})</h3>`;
      const div = document.createElement('div');
      renderReport(r, div);
      html += div.outerHTML;
    });
    out.innerHTML = html;
  } catch(e) { out.innerHTML = `<div class="error">${e.message}</div>`; }
}

// ── History ───────────────────────────────────────────────────────────────────
async function loadHistory() {
  const slug = document.getElementById('history-slug').value.trim();
  const out  = document.getElementById('history-result');
  if (!slug) { out.innerHTML='<div class="error">Enter a project slug.</div>'; return; }
  out.innerHTML = '<p class="loading">Loading…</p>';
  const res  = await apiFetch('/api/history/'+encodeURIComponent(slug));
  const data = await res.json();
  if (data.error) { out.innerHTML=`<div class="error">${data.error}</div>`; return; }

  // Load meta (tags+annotations)
  let meta = {};
  try {
    const mr = await apiFetch('/api/history/'+encodeURIComponent(slug)+'/meta');
    const md = await mr.json();
    meta = md.meta || {};
  } catch(_) {}

  const pdfLink = document.getElementById('pdf-link');
  pdfLink.style.display = 'inline-block';
  pdfLink.href = '/api/report/'+encodeURIComponent(slug)+'/pdf';
  pdfLink.textContent = '⬇ PDF';

  const xlsxLink = document.getElementById('xlsx-link');
  xlsxLink.style.display = 'inline-block';
  xlsxLink.style.marginLeft = '0';
  xlsxLink.textContent = '⬇ .xlsx';
  xlsxLink.onclick = () => downloadWithAuth('/api/report/'+encodeURIComponent(slug)+'/xlsx', slug+'_report.xlsx');

  const docxLink = document.getElementById('docx-link');
  docxLink.style.display = 'inline-block';
  docxLink.textContent = '⬇ .docx';
  docxLink.onclick = () => downloadWithAuth('/api/report/'+encodeURIComponent(slug)+'/docx', slug+'_report.docx');

  const emailBtn = document.getElementById('email-btn');
  emailBtn.style.display = 'inline-block';
  window._currentHistorySlug = slug;

  // Show compare-any section
  document.getElementById('compare-any-section').style.display = 'block';
  document.getElementById('cmp-idx-b').max = Math.max(0, data.count - 1);

  let html = `<p style="font-size:.85rem"><b>${data.count}</b> snapshot(s) for <code>${slug}</code></p>`;
  data.snapshots.slice().reverse().forEach((r, ri) => {
    const idx = data.count - 1 - ri;
    const m = meta[String(idx)] || {};
    const tagHtml = m.tag ? `<span style="background:var(--navy);color:#fff;font-size:.7rem;padding:.15rem .5rem;border-radius:10px;margin-left:.5rem">${m.tag}</span>` : '';
    const annoHtml = m.annotation ? `<div style="font-size:.78rem;color:#666;margin:.3rem 0;font-style:italic">💬 ${m.annotation}</div>` : '';
    const div = document.createElement('div'); div.style.marginTop='1rem';
    renderReport(r, div);
    html += `<details>
      <summary style="cursor:pointer;font-weight:700;margin-top:.8rem;font-size:.88rem">
        #${idx} · ${r.generated_at} — Score ${r.overall_score}/100 [${r.overall_risk}]${tagHtml}
      </summary>
      ${annoHtml}
      <div style="margin:.5rem 0;display:flex;gap:.4rem;flex-wrap:wrap">
        <input type="text" placeholder="Tag (e.g. post-audit)" id="tag-${idx}" value="${m.tag||''}" style="width:140px;padding:.3rem .5rem;font-size:.8rem;border:1px solid var(--border);border-radius:4px">
        <input type="text" placeholder="Annotation…" id="ann-${idx}" value="${m.annotation||''}" style="width:220px;padding:.3rem .5rem;font-size:.8rem;border:1px solid var(--border);border-radius:4px">
        <button onclick="saveSnapshotMeta('${slug}',${idx})" style="margin:0;padding:.3rem .7rem;font-size:.78rem">💾 Save</button>
      </div>
      ${div.outerHTML}
    </details>`;
  });
  out.innerHTML = html;
}

async function saveSnapshotMeta(slug, idx) {
  const tag = (document.getElementById('tag-'+idx)?.value||'').trim();
  const ann = (document.getElementById('ann-'+idx)?.value||'').trim();
  try {
    await apiFetch('/api/history/'+encodeURIComponent(slug)+'/meta', {
      method: 'POST', body: JSON.stringify({idx, tag, annotation: ann})
    });
    // Re-load to refresh tags
    loadHistory();
  } catch(e) { alert(e.message); }
}

async function loadCompareAny() {
  const slug = document.getElementById('history-slug').value.trim();
  const out  = document.getElementById('history-result');
  const idxA = parseInt(document.getElementById('cmp-idx-a').value)||0;
  const idxB = parseInt(document.getElementById('cmp-idx-b').value)||1;
  if (!slug) { out.innerHTML='<div class="error">Enter a project slug.</div>'; return; }
  out.innerHTML = '<p class="loading">Comparing snapshots…</p>';
  const res  = await apiFetch('/api/compare/'+encodeURIComponent(slug)+'/any', {
    method: 'POST', body: JSON.stringify({idx_a: idxA, idx_b: idxB})
  });
  const data = await res.json();
  if (data.error) { out.innerHTML=`<div class="error">${data.error}</div>`; return; }
  let html = `<h3 style="font-size:.95rem">Comparison #${idxA} vs #${idxB}: ${data.project_name}</h3>
    <p style="font-size:.82rem;color:#777;margin-bottom:.5rem">${data.version_a_date} → ${data.version_b_date}</p>
    <table class="diff-table"><thead><tr><th>Field</th><th>Old</th><th>New</th><th>Δ</th><th>Direction</th></tr></thead><tbody>`;
  data.deltas.forEach(d => {
    html += `<tr><td>${d.label}</td><td>${d.old_value}</td><td>${d.new_value}</td>
      <td>${d.delta!=null?(d.delta>0?'+':'')+d.delta:'—'}</td>
      <td class="tag-${d.direction}">${d.direction.toUpperCase()}</td></tr>`;
  });
  html += `</tbody></table><p style="margin-top:.8rem;font-size:.88rem"><b>Summary:</b> ${data.summary}</p>`;
  out.innerHTML = html;
}

function showEmailModal() {
  document.getElementById('email-modal').style.display = 'flex';
  document.getElementById('email-modal-result').textContent = '';
}

async function sendEmailReport() {
  const slug = window._currentHistorySlug || document.getElementById('history-slug').value.trim();
  const to   = document.getElementById('email-to-modal').value.trim();
  const res  = document.getElementById('email-modal-result');
  if (!to) { res.textContent = 'Enter a recipient email.'; res.style.color='var(--red)'; return; }
  res.textContent = 'Sending…'; res.style.color = '#999';
  try {
    const r = await apiFetch('/api/report/'+encodeURIComponent(slug)+'/email', {
      method: 'POST', body: JSON.stringify({to})
    });
    const d = await r.json();
    if (d.ok) { res.textContent='✓ Sent!'; res.style.color='var(--green)'; }
    else { res.textContent='✗ '+d.error; res.style.color='var(--red)'; }
  } catch(e) { res.textContent='✗ '+e.message; res.style.color='var(--red)'; }
}

async function loadCompare() {
  const slug = document.getElementById('history-slug').value.trim();
  const out  = document.getElementById('history-result');
  if (!slug) { out.innerHTML='<div class="error">Enter a project slug.</div>'; return; }
  out.innerHTML = '<p class="loading">Comparing…</p>';
  const res  = await apiFetch('/api/compare/'+encodeURIComponent(slug));
  const data = await res.json();
  if (data.error) { out.innerHTML=`<div class="error">${data.error}</div>`; return; }
  let html = `<h3 style="font-size:.95rem">Comparison: ${data.project_name}</h3>
    <p style="font-size:.82rem;color:#777;margin-bottom:.5rem">${data.version_a_date} → ${data.version_b_date}</p>
    <table class="diff-table"><thead><tr><th>Field</th><th>Old</th><th>New</th><th>Δ</th><th>Direction</th></tr></thead><tbody>`;
  data.deltas.forEach(d => {
    html += `<tr><td>${d.label}</td><td>${d.old_value}</td><td>${d.new_value}</td>
      <td>${d.delta!=null?(d.delta>0?'+':'')+d.delta:'—'}</td>
      <td class="tag-${d.direction}">${d.direction.toUpperCase()}</td></tr>`;
  });
  html += `</tbody></table><p style="margin-top:.8rem;font-size:.88rem"><b>Summary:</b> ${data.summary}</p>`;
  out.innerHTML = html;
}

async function listProjects() {
  const out = document.getElementById('projects-list');
  const res  = await apiFetch('/api/projects');
  const data = await res.json();
  if (!data.projects.length) { out.innerHTML='<p style="color:#999;font-size:.88rem">No projects with history yet.</p>'; return; }
  out.innerHTML = '<ul style="margin-top:.5rem">'+data.projects.map(s=>`<li style="margin:.3rem 0"><a href="#" style="color:var(--navy);font-size:.88rem" onclick="document.getElementById('history-slug').value='${s}';switchTab('history');return false">${s}</a></li>`).join('')+'</ul>';
}

async function downloadTemplate() { window.location.href = '/api/template/csv'; }

async function downloadWithAuth(url, filename) {
  try {
    const res  = await apiFetch(url);
    const blob = await res.blob();
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
  } catch(e) { alert(e.message); }
}

// ── Donor Comparison ──────────────────────────────────────────────────────────
async function loadDonorComparison() {
  const out = document.getElementById('donor-result');
  out.innerHTML = '<p class="loading">Loading donor comparison data…</p>';
  try {
    const res    = await apiFetch('/api/projects');
    const data   = await res.json();
    const slugs  = data.projects || [];
    if (!slugs.length) { out.innerHTML='<p style="color:#999">No projects yet.</p>'; return; }
    const reports = await Promise.all(slugs.map(async s => {
      const r = await apiFetch('/api/history/'+encodeURIComponent(s));
      const d = await r.json();
      return d.snapshots ? d.snapshots[d.snapshots.length-1] : null;
    }));
    const valid = reports.filter(Boolean);
    const donors = {};
    valid.forEach(r => {
      const raw = (r.donor || 'Unknown').trim();
      let key = raw;
      if (/adb|asian development/i.test(raw)) key='ADB';
      else if (/world bank|ibrd|ida/i.test(raw)) key='World Bank';
      else if (/usaid|united states agency/i.test(raw)) key='USAID';
      donors[key] = donors[key] || [];
      donors[key].push(r);
    });
    let html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;margin-bottom:1.4rem">';
    for (const [donor, rpts] of Object.entries(donors)) {
      const n = rpts.length;
      const avgScore = Math.round(rpts.reduce((a,r)=>a+r.overall_score,0)/n);
      const highCrit = rpts.filter(r=>['High','Critical'].includes(r.overall_risk)).length;
      const leakageH = rpts.filter(r=>['High','Critical'].includes(r.leakage.risk_level)).length;
      const sovH     = rpts.filter(r=>['High','Critical'].includes(r.sovereign.risk_level)).length;
      const col = riskColor(avgScore>=70?'Low':avgScore>=50?'Medium':'High');
      html += `<div style="border:1px solid var(--border);border-radius:8px;padding:1rem;background:#FFF;border-top:3px solid ${col}">
        <div style="font-weight:700;color:var(--navy);margin-bottom:.4rem">${donor}</div>
        <div style="font-size:.78rem;color:#999">${n} project${n===1?'':'s'}</div>
        <div style="display:flex;align-items:center;gap:.5rem;margin:.6rem 0">
          <div style="flex:1;height:8px;background:#EEE;border-radius:4px">
            <div style="width:${avgScore}%;height:100%;background:${col};border-radius:4px"></div>
          </div>
          <b style="color:${col};font-family:var(--mono)">${avgScore}</b>
        </div>
        <div style="font-size:.75rem;color:#999">High/Critical: ${highCrit}/${n}</div>
        ${highCrit>0?`<div style="font-size:.73rem;color:var(--orange);margin-top:.3rem">⚠ ${leakageH>=sovH?'Leakage pattern':'Sovereignty issues'}</div>`:'<div style="font-size:.73rem;color:var(--green);margin-top:.3rem">✓ Generally low risk</div>'}
      </div>`;
    }
    html += '</div><table style="width:100%;border-collapse:collapse;font-size:.82rem"><thead><tr style="background:var(--navy);color:#fff"><th style="padding:.5rem;text-align:left">Project</th><th style="padding:.5rem">Country</th><th style="padding:.5rem">Donor</th><th style="padding:.5rem">Score</th><th style="padding:.5rem">Risk</th><th style="padding:.5rem">Leakage</th><th style="padding:.5rem">Impact</th><th style="padding:.5rem">Sovereign</th></tr></thead><tbody>';
    valid.forEach((r,i) => {
      const bg = i%2===0?'#F9FAFB':'#FFF';
      const rc = riskColor(r.overall_risk);
      html += `<tr style="background:${bg}"><td style="padding:.4rem .5rem">${r.project_name}</td><td style="padding:.4rem .5rem;text-align:center">${r.country}</td><td style="padding:.4rem .5rem">${r.donor||'—'}</td><td style="padding:.4rem .5rem;text-align:center;background:${rc};color:#fff;font-weight:700;font-family:var(--mono)">${r.overall_score}</td><td style="padding:.4rem .5rem;text-align:center;background:${rc};color:#fff;font-weight:700">${r.overall_risk}</td><td style="padding:.4rem .5rem;text-align:center;font-family:var(--mono)">${r.leakage.integrity_score}</td><td style="padding:.4rem .5rem;text-align:center;font-family:var(--mono)">${r.impact.integrity_score}</td><td style="padding:.4rem .5rem;text-align:center;font-family:var(--mono)">${r.sovereign.integrity_score}</td></tr>`;
    });
    html += '</tbody></table>';
    out.innerHTML = html;
  } catch(e) { out.innerHTML = `<div class="error">${e.message}</div>`; }
}

// ── Risk Trend Dashboard ──────────────────────────────────────────────────────
const TREND_LABELS = {
  en: { nodata: 'No trend data available yet. Run analyses first.', countries: 'Countries', projects: 'Projects', critical: 'Critical', improving: 'Improving' },
  fr: { nodata: 'Aucune donnée disponible.', countries: 'Pays', projects: 'Projets', critical: 'Critique', improving: 'Amélioration' },
  ar: { nodata: 'لا توجد بيانات متاحة.', countries: 'الدول', projects: 'المشاريع', critical: 'حرج', improving: 'تحسن' },
};
const RISK_LABELS = {
  en: {Low:'Low',Medium:'Medium',High:'High',Critical:'Critical'},
  fr: {Low:'Faible',Medium:'Moyen',High:'Élevé',Critical:'Critique'},
  ar: {Low:'منخفض',Medium:'متوسط',High:'مرتفع',Critical:'حرج'},
};

async function loadRiskTrends() {
  const out = document.getElementById('trend-result');
  out.innerHTML = '<p class="loading">Loading risk trend data…</p>';
  try {
    const res  = await apiFetch('/api/risk-trends');
    const data = await res.json();
    _trendRaw  = data;
    renderTrendUI();
  } catch(e) { document.getElementById('trend-result').innerHTML = `<div class="error">${e.message}</div>`; }
}

function renderTrendUI() {
  if (!_trendRaw) return;
  const lang       = document.getElementById('trend-lang')?.value || 'en';
  const search     = (document.getElementById('trend-search')?.value || '').toLowerCase();
  const sort       = document.getElementById('trend-sort')?.value || 'score_asc';
  const labels     = TREND_LABELS[lang] || TREND_LABELS.en;
  const riskNames  = RISK_LABELS[lang]  || RISK_LABELS.en;
  const dir        = lang === 'ar' ? 'rtl' : 'ltr';
  const countries  = _trendRaw.countries || {};
  const out        = document.getElementById('trend-result');
  const sumBar     = document.getElementById('trend-summary-bar');

  if (!Object.keys(countries).length) {
    sumBar.innerHTML = '';
    out.innerHTML = `<p style="color:#999;font-size:.88rem">${labels.nodata}</p>`;
    return;
  }

  // Destroy old chart instances
  Object.values(_trendCharts).forEach(c => { try { c.destroy(); } catch(_) {} });
  _trendCharts = {};

  // Aggregate stats
  let totalProjects = 0, criticalCount = 0, improvingCount = 0;
  const countryList = [];

  for (const [country, series] of Object.entries(countries)) {
    if (!series.length) continue;
    const latest  = series[series.length-1];
    const first   = series[0];
    const delta   = latest.avg_score - first.avg_score;
    totalProjects += series.reduce((a,s)=>a+s.project_count,0);
    if (['High','Critical'].includes(latest.risk_level)) criticalCount++;
    if (delta > 1) improvingCount++;
    countryList.push({ country, series, latest, delta });
  }

  // Filter
  const filtered = countryList.filter(c => !search || c.country.toLowerCase().includes(search));

  // Sort
  filtered.sort((a,b) => {
    switch(sort) {
      case 'score_asc':   return a.latest.avg_score - b.latest.avg_score;
      case 'score_desc':  return b.latest.avg_score - a.latest.avg_score;
      case 'country_az':  return a.country.localeCompare(b.country);
      case 'trend_worst': return a.delta - b.delta;
      default:            return 0;
    }
  });

  // Summary bar
  sumBar.innerHTML = `
    <div class="trend-summary-bar">
      <div class="ts-item"><div class="ts-num">${Object.keys(countries).length}</div><div class="ts-lbl">${labels.countries}</div></div>
      <div class="ts-item"><div class="ts-num">${totalProjects}</div><div class="ts-lbl">${labels.projects}</div></div>
      <div class="ts-item"><div class="ts-num" style="color:#E87070">${criticalCount}</div><div class="ts-lbl">${labels.critical}</div></div>
      <div class="ts-item"><div class="ts-num" style="color:#6EE7B7">${improvingCount}</div><div class="ts-lbl">${labels.improving}</div></div>
    </div>`;

  if (!filtered.length) {
    out.innerHTML = '<p style="color:#999;font-size:.88rem">No countries match your filter.</p>'; return;
  }

  // Build cards
  let html = `<div class="trend-grid" dir="${dir}">`;
  filtered.forEach(({ country, series, latest, delta }) => {
    const trend      = series.length > 1 ? (delta > 1 ? '↑' : delta < -1 ? '↓' : '→') : '→';
    const trendColor = delta > 1 ? 'var(--green)' : delta < -1 ? 'var(--red)' : '#999';
    const latestRisk = latest.risk_level || 'Low';
    const canvasId   = 'chart-' + country.replace(/[^a-z0-9]/gi,'_');
    const projectList = series.flatMap(pt => pt.projects).filter((v,i,a)=>a.indexOf(v)===i).slice(0,4).join(', ');

    html += `<div class="trend-card risk-${latestRisk}">
      <div class="trend-card-header">
        <div>
          <div class="trend-country">${country}</div>
          <div class="trend-meta">${series.length} data point${series.length===1?'':'s'} · ${series.reduce((a,s)=>a+s.project_count,0)} analyses</div>
        </div>
        <div>
          <div class="trend-delta" style="color:${trendColor}">${trend} ${Math.abs(delta).toFixed(1)}</div>
          <div style="font-size:.7rem;color:#AAA;text-align:right">${series.length>1?'vs. first':'single pt'}</div>
        </div>
      </div>
      <div class="trend-chart-wrap"><canvas id="${canvasId}"></canvas></div>
      <div class="trend-score-row">
        <div>
          <div style="font-size:.7rem;color:#999;text-transform:uppercase;letter-spacing:.05em">Latest Score</div>
          <div class="trend-score" style="color:${riskColor(latestRisk)}">${latest.avg_score}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:.7rem;color:#999;text-transform:uppercase;letter-spacing:.05em">Risk</div>
          <span class="risk-badge risk-${latestRisk}" style="margin-top:.2rem">${riskNames[latestRisk]||latestRisk}</span>
        </div>
      </div>
      ${projectList ? `<div style="font-size:.68rem;color:#AAA;margin-top:.5rem;line-height:1.4">📁 ${projectList}${series.flatMap(pt=>pt.projects).filter((v,i,a)=>a.indexOf(v)===i).length>4?' …':''}</div>` : ''}
    </div>`;
  });
  html += '</div>';
  out.innerHTML = html;

  // Render sparkline charts after DOM update
  requestAnimationFrame(() => {
    filtered.forEach(({ country, series }) => {
      const canvasId = 'chart-' + country.replace(/[^a-z0-9]/gi,'_');
      const canvas   = document.getElementById(canvasId);
      if (!canvas || series.length < 2) return;
      const labels_  = series.map(pt => pt.date.slice(5));   // MM-DD
      const scores   = series.map(pt => pt.avg_score);
      const colors   = series.map(pt => riskColor(pt.risk_level));
      _trendCharts[country] = new Chart(canvas, {
        type: 'line',
        data: {
          labels: labels_,
          datasets: [{
            data: scores,
            borderColor: riskColor(series[series.length-1].risk_level),
            backgroundColor: 'transparent',
            pointBackgroundColor: colors,
            pointBorderColor: '#fff',
            pointBorderWidth: 1.5,
            pointRadius: 4,
            tension: 0.35,
            borderWidth: 2,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: {
            callbacks: { label: ctx => `Score: ${ctx.parsed.y}` },
            backgroundColor: '#0D1B2A', titleFont: { family: "'IBM Plex Mono'" },
            bodyFont: { family: "'IBM Plex Mono'" },
          }},
          scales: {
            x: { display: true, ticks: { font: { size: 9, family: "'IBM Plex Mono'" }, color: '#AAA', maxTicksLimit: 4 }, grid: { display: false } },
            y: { display: false, min: 0, max: 100 },
          },
          animation: { duration: 400 },
        },
      });
    });
  });
}

// ── Alerts ────────────────────────────────────────────────────────────────────
async function loadAlertSettings() {
  try {
    const res  = await apiFetch('/api/alerts/settings');
    const data = await res.json();
    const no   = data.notify_on || ['Critical'];
    document.getElementById('notify-critical').checked = no.includes('Critical');
    document.getElementById('notify-high').checked     = no.includes('High');
    document.getElementById('notify-medium').checked   = no.includes('Medium');
    const em = data.email || {};
    document.getElementById('email-enabled').checked   = !!em.enabled;
    document.getElementById('email-smtp-host').value   = em.smtp_host || 'smtp.gmail.com';
    document.getElementById('email-smtp-port').value   = em.smtp_port || 587;
    document.getElementById('email-username').value    = em.username  || '';
    document.getElementById('email-password').value    = em.password  || '';
    document.getElementById('email-from').value        = em.from_addr || '';
    document.getElementById('email-to').value          = (em.to_addrs || []).join(', ');
    const tg = data.telegram || {};
    document.getElementById('tg-enabled').checked = !!tg.enabled;
    document.getElementById('tg-token').value     = tg.bot_token || '';
    document.getElementById('tg-chat-id').value   = tg.chat_id   || '';
  } catch(e) { console.error('Alert settings load failed', e); }
}

async function saveAlertSettings() {
  const notifyOn = [];
  if (document.getElementById('notify-critical').checked) notifyOn.push('Critical');
  if (document.getElementById('notify-high').checked)     notifyOn.push('High');
  if (document.getElementById('notify-medium').checked)   notifyOn.push('Medium');
  const payload = {
    notify_on: notifyOn,
    email: {
      enabled: document.getElementById('email-enabled').checked,
      smtp_host: document.getElementById('email-smtp-host').value.trim(),
      smtp_port: parseInt(document.getElementById('email-smtp-port').value)||587,
      username: document.getElementById('email-username').value.trim(),
      password: document.getElementById('email-password').value,
      from_addr: document.getElementById('email-from').value.trim(),
      to_addrs: document.getElementById('email-to').value.split(',').map(s=>s.trim()).filter(Boolean),
    },
    telegram: {
      enabled: document.getElementById('tg-enabled').checked,
      bot_token: document.getElementById('tg-token').value.trim(),
      chat_id: document.getElementById('tg-chat-id').value.trim(),
    },
  };
  const out = document.getElementById('alert-save-result');
  out.innerHTML = '<p class="loading">Saving…</p>';
  try {
    const res  = await apiFetch('/api/alerts/settings', { method:'POST', body: JSON.stringify(payload) });
    const data = await res.json();
    out.innerHTML = data.ok ? '<div class="success">✓ Settings saved</div>' : `<div class="error">${data.error}</div>`;
  } catch(e) { out.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function testAlert(channel) {
  const id  = channel==='email'?'email-test-result':'tg-test-result';
  const out = document.getElementById(id);
  out.textContent = 'Sending…'; out.style.color = '#999';
  try {
    const res  = await apiFetch('/api/alerts/test/'+channel, {method:'POST'});
    const data = await res.json();
    if (data.ok) { out.textContent='✓ Sent!'; out.style.color='var(--green)'; }
    else         { out.textContent='✗ '+data.error; out.style.color='var(--red)'; }
  } catch(e)   { out.textContent='✗ '+e.message; out.style.color='var(--red)'; }
}

// ── Sector Benchmarks ─────────────────────────────────────────────────────────
async function loadSectorBenchmarks() {
  const out = document.getElementById('benchmark-result');
  out.innerHTML = '<p class="loading">Loading sector benchmarks…</p>';
  try {
    const res  = await apiFetch('/api/sector-benchmarks');
    const data = await res.json();
    const sectors = data.sectors || {};
    if (!Object.keys(sectors).length) {
      out.innerHTML = '<p style="color:#999;font-size:.85rem">No data yet. Analyze some projects first.</p>'; return;
    }
    let html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:1rem">';
    for (const [sector, info] of Object.entries(sectors)) {
      const col = riskColor(info.avg_score>=70?'Low':info.avg_score>=50?'Medium':'High');
      html += `<div style="border:1px solid var(--border);border-radius:8px;padding:1rem;border-top:3px solid ${col}">
        <div style="font-weight:700;margin-bottom:.4rem;color:var(--navy)">${sector}</div>
        <div style="font-size:.75rem;color:#999;margin-bottom:.7rem">${info.count} project${info.count===1?'':'s'}</div>
        <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem">
          <span style="font-size:.72rem;color:#777;width:80px">Avg Score</span>
          <div style="flex:1;height:7px;background:#EEE;border-radius:4px"><div style="width:${info.avg_score}%;height:100%;background:${col};border-radius:4px"></div></div>
          <b style="font-family:var(--mono);font-size:.82rem;color:${col}">${info.avg_score}</b>
        </div>
        <div style="display:flex;gap:.5rem;font-size:.72rem;color:#999;font-family:var(--mono)">
          <span>Leak: <b>${info.avg_leakage}</b></span>
          <span>Impact: <b>${info.avg_impact}</b></span>
          <span>Sov: <b>${info.avg_sovereign}</b></span>
        </div>
        ${info.high_critical_count>0?`<div style="font-size:.72rem;color:var(--orange);margin-top:.4rem">⚠ ${info.high_critical_count} high/critical</div>`:''}
      </div>`;
    }
    html += '</div>';
    // Comparison bar chart
    html += `<div style="margin-top:1.2rem"><canvas id="sector-chart" height="80"></canvas></div>`;
    out.innerHTML = html;
    // Draw chart
    requestAnimationFrame(() => {
      const ctx = document.getElementById('sector-chart');
      if (!ctx) return;
      new Chart(ctx, {
        type: 'bar',
        data: {
          labels: Object.keys(sectors),
          datasets: [
            { label: 'Avg Score', data: Object.values(sectors).map(s=>s.avg_score), backgroundColor: Object.values(sectors).map(s=>riskColor(s.avg_score>=70?'Low':s.avg_score>=50?'Medium':'High'))+'99' },
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { min: 0, max: 100 } }
        }
      });
    });
  } catch(e) { out.innerHTML = `<div class="error">${e.message}</div>`; }
}

// ── Anomaly Detection ─────────────────────────────────────────────────────────
async function loadAnomalies() {
  const out = document.getElementById('anomaly-result');
  out.innerHTML = '<p class="loading">Scanning for anomalies…</p>';
  try {
    const res  = await apiFetch('/api/anomalies');
    const data = await res.json();
    const anomalies = data.anomalies || [];
    if (!anomalies.length) {
      out.innerHTML = '<div class="success">✓ No sudden score drops detected across all projects.</div>'; return;
    }
    let html = `<p style="font-size:.85rem;color:#777;margin-bottom:.8rem">Found <b>${anomalies.length}</b> anomal${anomalies.length===1?'y':'ies'} (≥10-point score drop)</p>`;
    html += '<table class="diff-table"><thead><tr><th>Project</th><th>Country</th><th>Date</th><th>Prev</th><th>New</th><th>Drop</th><th>Risk Change</th></tr></thead><tbody>';
    anomalies.forEach(a => {
      html += `<tr>
        <td><a href="#" onclick="document.getElementById('history-slug').value='${a.slug}';switchTab('history');return false" style="color:var(--navy)">${a.project}</a></td>
        <td>${a.country}</td>
        <td style="font-family:var(--mono);font-size:.8rem">${a.date}</td>
        <td style="font-family:var(--mono)">${a.prev_score}</td>
        <td style="font-family:var(--mono)">${a.curr_score}</td>
        <td style="font-family:var(--mono);color:var(--red);font-weight:700">${a.delta}</td>
        <td><span class="risk-badge risk-${a.prev_risk}" style="font-size:.7rem">${a.prev_risk}</span> → <span class="risk-badge risk-${a.curr_risk}" style="font-size:.7rem">${a.curr_risk}</span></td>
      </tr>`;
    });
    html += '</tbody></table>';
    out.innerHTML = html;
  } catch(e) { out.innerHTML = `<div class="error">${e.message}</div>`; }
}

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  updateHeaderUI();
  if (SESSION_TOKEN) {
    // Validate stored session
    try {
      const res = await fetch('/api/auth/me', { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        SESSION_USER = d.username; SESSION_ORG = d.org; SESSION_ROLE = d.role;
        updateHeaderUI();
        document.getElementById('login-overlay').classList.add('hidden');
      } else {
        clearSession(); updateHeaderUI();
      }
    } catch(_) { clearSession(); updateHeaderUI(); }
  }
  // Enter key support on login
  document.getElementById('l-pass').addEventListener('keydown', e => { if (e.key==='Enter') doLogin(); });
  document.getElementById('l-user').addEventListener('keydown', e => { if (e.key==='Enter') doLogin(); });
})();
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"Budget Integrity Monitor v5 running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
