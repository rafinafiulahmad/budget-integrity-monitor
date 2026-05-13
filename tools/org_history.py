"""
Org-scoped history layer.

Wraps the base history functions to namespace files under:
  reports/history/{org_slug}/{project_slug}.jsonl

Falls back to the global HISTORY_DIR for backward-compat reads.
"""

import json
import re
from pathlib import Path
from typing import Optional

from core.models import IntegrityReport, RiskLevel
from tools.history import (
    compare_snapshots, compare_latest_two as _base_compare,
    HistoricalComparison, ScoreDelta,
)

BASE_HISTORY = Path("reports/history")


def _org_slug(org: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", org.lower()).strip("_")

def _proj_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def _history_dir(org: str) -> Path:
    d = BASE_HISTORY / _org_slug(org)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _history_file(org: str, project_name: str) -> Path:
    return _history_dir(org) / f"{_proj_slug(project_name)}.jsonl"


# ── persistence ───────────────────────────────────────────────────────────────

def save_report(report: IntegrityReport, org: str) -> None:
    path = _history_file(org, report.project_name)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(report.to_json().replace("\n", " ") + "\n")


def load_history(project_name: str, org: str) -> list[dict]:
    path = _history_file(org, project_name)
    if not path.exists():
        return []
    snapshots = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return snapshots


def list_projects(org: str) -> list[str]:
    d = _history_dir(org)
    return [p.stem for p in d.glob("*.jsonl")]


def compare_latest_two(project_name: str, org: str) -> Optional[HistoricalComparison]:
    history = load_history(project_name, org)
    if len(history) < 2:
        return None
    return compare_snapshots(history[-2], history[-1])


def compare_any_two(project_name: str, org: str, idx_a: int, idx_b: int) -> Optional[HistoricalComparison]:
    """Compare any two snapshots by index."""
    history = load_history(project_name, org)
    if len(history) < 2:
        return None
    if idx_a < 0 or idx_b < 0 or idx_a >= len(history) or idx_b >= len(history):
        return None
    return compare_snapshots(history[idx_a], history[idx_b])


# ── snapshot metadata (tags & annotations) ────────────────────────────────────

def _meta_file(org: str, project_name: str) -> Path:
    return _history_dir(org) / f"{_proj_slug(project_name)}.meta.json"


def load_snapshot_meta(project_name: str, org: str) -> dict:
    """Return {idx: {tag, annotation}} dict."""
    p = _meta_file(org, project_name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_snapshot_meta(project_name: str, org: str, meta: dict) -> None:
    p = _meta_file(org, project_name)
    p.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ── risk-trend aggregation ────────────────────────────────────────────────────

def get_risk_trends(org: str) -> dict:
    """Country-level aggregate risk scores over time, scoped to org."""
    slugs = list_projects(org)
    country_timeline: dict = {}

    for slug in slugs:
        snaps = load_history(slug, org)
        for snap in snaps:
            country = snap.get("country", "Unknown")
            ts      = snap.get("generated_at", "")[:10]
            score   = snap.get("overall_score", 0)
            risk    = snap.get("overall_risk", "")
            country_timeline.setdefault(country, {}).setdefault(ts, []).append({
                "score": score, "risk": risk,
                "project": snap.get("project_name", ""),
            })

    result = {}
    for country, date_map in country_timeline.items():
        series = []
        for dt in sorted(date_map.keys()):
            entries = date_map[dt]
            avg_score = sum(e["score"] for e in entries) / len(entries)
            risks = [e["risk"] for e in entries]
            severity = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
            worst_risk = max(risks, key=lambda r: severity.get(r, 0))
            series.append({
                "date": dt,
                "avg_score": round(avg_score, 1),
                "risk_level": worst_risk,
                "project_count": len(entries),
                "projects": [e["project"] for e in entries],
            })
        result[country] = series

    return {"countries": result, "total_countries": len(result)}
