"""
Historical Comparison Engine
Stores IntegrityReport snapshots and diffs them across versions.

Storage: simple JSON-lines file per project (slug-based).
Each line = one serialised IntegrityReport (with timestamp).
"""

import json
import re
import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from core.models import IntegrityReport, RiskLevel


HISTORY_DIR = Path("reports/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _history_file(project_name: str) -> Path:
    return HISTORY_DIR / f"{_slug(project_name)}.jsonl"


# ── persistence ───────────────────────────────────────────────────────────────

def save_report(report: IntegrityReport) -> None:
    """Append a report snapshot to the project's history file."""
    path = _history_file(report.project_name)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(report.to_json().replace("\n", " ") + "\n")


def load_history(project_name: str) -> list[dict]:
    """Return all historical snapshots for a project (oldest first)."""
    path = _history_file(project_name)
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


def list_projects() -> list[str]:
    """Return all project slugs that have history files."""
    return [p.stem for p in HISTORY_DIR.glob("*.jsonl")]


# ── diff / comparison ────────────────────────────────────────────────────────

@dataclass
class ScoreDelta:
    field: str
    label: str
    old_value: float | str
    new_value: float | str
    delta: Optional[float]          # None for non-numeric
    direction: str                  # "improved" | "worsened" | "unchanged" | "n/a"
    is_risk_flag: bool = False


@dataclass
class HistoricalComparison:
    project_name: str
    version_a_date: str
    version_b_date: str
    deltas: list[ScoreDelta]
    summary: str

    def to_text(self) -> str:
        lines = [
            f"HISTORICAL COMPARISON — {self.project_name}",
            f"{'='*60}",
            f"Version A : {self.version_a_date}",
            f"Version B : {self.version_b_date}",
            "",
            f"{'Field':<35} {'Old':>10} {'New':>10} {'Δ':>8}  Direction",
            "-"*75,
        ]
        for d in self.deltas:
            delta_str = f"{d.delta:+.1f}" if d.delta is not None else "—"
            lines.append(
                f"{d.label:<35} {str(d.old_value):>10} {str(d.new_value):>10} "
                f"{delta_str:>8}  {d.direction.upper()}"
            )
        lines += ["", "Summary:", self.summary, "="*60]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "version_a_date": self.version_a_date,
            "version_b_date": self.version_b_date,
            "deltas": [
                {
                    "field": d.field,
                    "label": d.label,
                    "old_value": d.old_value,
                    "new_value": d.new_value,
                    "delta": d.delta,
                    "direction": d.direction,
                }
                for d in self.deltas
            ],
            "summary": self.summary,
        }


def _direction(delta: float | None, higher_is_better: bool = True) -> str:
    if delta is None:
        return "n/a"
    if abs(delta) < 0.5:
        return "unchanged"
    improved = delta > 0 if higher_is_better else delta < 0
    return "improved" if improved else "worsened"


def compare_snapshots(snap_a: dict, snap_b: dict) -> HistoricalComparison:
    """Diff two raw snapshot dicts (from load_history)."""

    def _score(snap, path):
        """Navigate dotted path, e.g. 'leakage.integrity_score'."""
        obj = snap
        for key in path.split("."):
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
        return obj

    comparisons = [
        # (field_path, label, higher_is_better)
        ("overall_score",                   "Overall Integrity Score",      True),
        ("overall_risk",                    "Overall Risk Level",           False),
        ("leakage.integrity_score",         "Leakage Score",                True),
        ("leakage.consultancy_pct",         "Consultancy % of Budget",      False),
        ("leakage.admin_overhead_pct",      "Admin Overhead %",             False),
        ("impact.integrity_score",          "Impact Score",                 True),
        ("impact.cost_per_beneficiary_usd", "Cost per Beneficiary (USD)",   False),
        ("sovereign.integrity_score",       "Sovereign Alignment Score",    True),
        ("sovereign.tied_aid_detected",     "Tied Aid Detected",            False),
        ("sovereign.conditionality_risk",   "Conditionality Risk",          False),
    ]

    deltas: list[ScoreDelta] = []
    for path, label, higher_is_better in comparisons:
        old_val = _score(snap_a, path)
        new_val = _score(snap_b, path)

        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
            delta = round(new_val - old_val, 2)
            dir_ = _direction(delta, higher_is_better)
        elif isinstance(old_val, bool) and isinstance(new_val, bool):
            delta = None
            if old_val == new_val:
                dir_ = "unchanged"
            elif higher_is_better:
                dir_ = "improved" if not new_val else "worsened"
            else:
                dir_ = "worsened" if new_val else "improved"
        else:
            delta = None
            dir_ = "unchanged" if old_val == new_val else "changed"

        deltas.append(ScoreDelta(
            field=path,
            label=label,
            old_value=old_val if old_val is not None else "—",
            new_value=new_val if new_val is not None else "—",
            delta=delta,
            direction=dir_,
        ))

    # Build a plain-language summary
    worsened = [d for d in deltas if d.direction == "worsened"]
    improved = [d for d in deltas if d.direction == "improved"]
    summary_parts = []
    if improved:
        names = ", ".join(d.label for d in improved[:3])
        summary_parts.append(f"Improvements in: {names}.")
    if worsened:
        names = ", ".join(d.label for d in worsened[:3])
        summary_parts.append(f"Deterioration in: {names} — warrants review.")
    if not summary_parts:
        summary_parts.append("No significant changes detected between versions.")

    return HistoricalComparison(
        project_name=snap_a.get("project_name", "Unknown"),
        version_a_date=snap_a.get("generated_at", "?"),
        version_b_date=snap_b.get("generated_at", "?"),
        deltas=deltas,
        summary=" ".join(summary_parts),
    )


def compare_latest_two(project_name: str) -> Optional[HistoricalComparison]:
    """Compare the two most recent snapshots for a project."""
    history = load_history(project_name)
    if len(history) < 2:
        return None
    return compare_snapshots(history[-2], history[-1])
