"""
Excel Export Tool
Generates formatted .xlsx reports with charts for Budget Integrity Monitor.
Supports single project reports and multi-project donor comparison exports.
"""

import io
import datetime
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, LineChart, RadarChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.drawing.image import Image as XLImage

# ── Color palette ─────────────────────────────────────────────────────────────

NAVY    = "0D1B2A"
GOLD    = "C9A84C"
WHITE   = "FFFFFF"
LGREY   = "F5F5F5"
MGREY   = "CCCCCC"
DGREY   = "555555"
GREEN   = "27AE60"
YELLOW  = "F39C12"
ORANGE  = "E67E22"
RED     = "C0392B"

RISK_COLORS = {"Low": GREEN, "Medium": YELLOW, "High": ORANGE, "Critical": RED}

# ── Style helpers ─────────────────────────────────────────────────────────────

def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)

def _font(bold=False, size=10, color=None, name="Arial"):
    return Font(name=name, bold=bold, size=size, color=color or "000000")

def _align(h="left", v="center", wrap=False):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

def _border(style="thin"):
    s = Side(style=style, color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

def _thick_border():
    t = Side(style="medium", color="0D1B2A")
    return Border(left=t, right=t, top=t, bottom=t)

def _header_cell(ws, row, col, text, bg=NAVY, fg=WHITE, size=11, bold=True, colspan=1):
    cell = ws.cell(row=row, column=col, value=text)
    cell.font = _font(bold=bold, size=size, color=fg)
    cell.fill = _fill(bg)
    cell.alignment = _align("center", "center")
    cell.border = _border()
    if colspan > 1:
        ws.merge_cells(
            start_row=row, start_column=col,
            end_row=row, end_column=col + colspan - 1
        )
    return cell

def _data_cell(ws, row, col, value, bg=None, fg=None, bold=False, align="left", number_format=None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = _font(bold=bold, color=fg or "222222")
    if bg:
        cell.fill = _fill(bg)
    cell.alignment = _align(align, "center", wrap=True)
    cell.border = _border()
    if number_format:
        cell.number_format = number_format
    return cell

def _set_col_widths(ws, widths: dict):
    for col_letter, width in widths.items():
        ws.column_dimensions[col_letter].width = width

def _risk_bg(risk_level: str) -> str:
    return RISK_COLORS.get(risk_level, MGREY)

# ── Sheet builders ─────────────────────────────────────────────────────────────

def _build_summary_sheet(wb: Workbook, report: dict):
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False

    # Title banner
    ws.row_dimensions[1].height = 40
    _header_cell(ws, 1, 1, "🏛 LDC Budget Integrity Monitor", colspan=6, size=16)

    ws.row_dimensions[2].height = 22
    _header_cell(ws, 2, 1, f"Integrity Report — {report.get('project_name', '')}", bg=GOLD, size=12, colspan=6)

    # Meta row
    ws.row_dimensions[3].height = 18
    meta = [
        ("Country", report.get("country", "—")),
        ("Donor", report.get("donor", "—")),
        ("Generated", report.get("generated_at", "—")),
    ]
    for i, (k, v) in enumerate(meta):
        _header_cell(ws, 3, i*2+1, k, bg=LGREY, fg=NAVY, size=9, bold=True)
        _data_cell(ws, 3, i*2+2, v, bg=WHITE, fg=DGREY, bold=False)

    ws.row_dimensions[4].height = 8  # spacer

    # Overall score + risk
    ws.row_dimensions[5].height = 36
    risk = report.get("overall_risk", "")
    score = report.get("overall_score", 0)
    _header_cell(ws, 5, 1, "OVERALL RISK", colspan=2, bg=_risk_bg(risk), size=13)
    _header_cell(ws, 5, 3, risk.upper(), colspan=2, bg=_risk_bg(risk), size=13)
    _header_cell(ws, 5, 5, f"Score: {score}/100", colspan=2, bg=NAVY, size=13)

    ws.row_dimensions[6].height = 8  # spacer

    # Sub-scores table
    ws.row_dimensions[7].height = 24
    headers = ["Dimension", "Risk Level", "Integrity Score", "Key Metric", "Value"]
    for i, h in enumerate(headers, 1):
        _header_cell(ws, 7, i, h, size=10)

    rows = [
        ("Leakage Detection",
         report["leakage"]["risk_level"],
         report["leakage"]["integrity_score"],
         "Consultancy %",
         f"{report['leakage']['consultancy_pct']}%"),
        ("Impact-to-Citizen",
         report["impact"]["risk_level"],
         report["impact"]["integrity_score"],
         "Cost/Beneficiary (USD)",
         f"${report['impact'].get('cost_per_beneficiary_usd') or '—'}"),
        ("Sovereign Alignment",
         report["sovereign"]["risk_level"],
         report["sovereign"]["integrity_score"],
         "Tied Aid",
         "Yes ⚠" if report["sovereign"]["tied_aid_detected"] else "No ✓"),
    ]

    for r_idx, (dim, risk_lv, score_v, metric, val) in enumerate(rows, 8):
        ws.row_dimensions[r_idx].height = 22
        bg = LGREY if r_idx % 2 == 0 else WHITE
        _data_cell(ws, r_idx, 1, dim, bg=bg, bold=True)
        cell = _data_cell(ws, r_idx, 2, risk_lv, bg=_risk_bg(risk_lv), fg=WHITE, bold=True, align="center")
        _data_cell(ws, r_idx, 3, score_v, bg=bg, align="center")
        _data_cell(ws, r_idx, 4, metric, bg=bg)
        _data_cell(ws, r_idx, 5, val, bg=bg, align="center")

    ws.row_dimensions[11].height = 8

    # Civil society summary
    _header_cell(ws, 12, 1, "Civil Society Summary", colspan=5, bg=GOLD, size=10)
    ws.row_dimensions[13].height = 60
    cell = ws.cell(row=13, column=1, value=report.get("civil_society_summary", ""))
    cell.font = _font(size=9, color=DGREY)
    cell.alignment = _align("left", "top", wrap=True)
    cell.fill = _fill(LGREY)
    ws.merge_cells(start_row=13, start_column=1, end_row=13, end_column=5)

    _set_col_widths(ws, {"A": 26, "B": 14, "C": 16, "D": 24, "E": 18, "F": 12})


def _build_findings_sheet(wb: Workbook, report: dict):
    ws = wb.create_sheet("Findings & Recommendations")
    ws.sheet_view.showGridLines = False

    _header_cell(ws, 1, 1, "Findings & Recommendations", colspan=3, size=14)
    ws.row_dimensions[1].height = 32

    row = 3
    sections = [
        ("🔍 Leakage Detection", report["leakage"]),
        ("👥 Impact-to-Citizen", report["impact"]),
        ("🏛 Sovereign Alignment", report["sovereign"]),
    ]

    for title, section in sections:
        ws.row_dimensions[row].height = 24
        _header_cell(ws, row, 1, title, colspan=3, bg=GOLD, size=11)
        row += 1

        for finding in section.get("findings", []):
            ws.row_dimensions[row].height = 18
            bg = LGREY if row % 2 == 0 else WHITE
            _data_cell(ws, row, 1, "•", bg=bg, align="center", bold=True)
            cell = ws.cell(row=row, column=2, value=finding)
            cell.font = _font(size=9)
            cell.fill = _fill(bg)
            cell.alignment = _align("left", "center", wrap=True)
            cell.border = _border()
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
            row += 1

        ws.row_dimensions[row].height = 18
        _header_cell(ws, row, 1, "→ Recommendation", bg=NAVY, size=9, colspan=1)
        cell = ws.cell(row=row, column=2, value=section.get("recommendation", ""))
        cell.font = _font(size=9, bold=True, color=NAVY)
        cell.fill = _fill("EAF4FF")
        cell.alignment = _align("left", "center", wrap=True)
        cell.border = _border()
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        row += 2

    _set_col_widths(ws, {"A": 4, "B": 50, "C": 20})


def _build_budget_breakdown_sheet(wb: Workbook, report: dict, line_items: list = None):
    ws = wb.create_sheet("Budget Breakdown")
    ws.sheet_view.showGridLines = False

    _header_cell(ws, 1, 1, "Budget Line Item Breakdown", colspan=5, size=14)
    ws.row_dimensions[1].height = 32

    headers = ["Line Item", "Category", "Vendor Origin", "Amount (USD)", "% of Total"]
    for i, h in enumerate(headers, 1):
        _header_cell(ws, 2, i, h, size=10)

    total_budget = report.get("total_budget_usd", 0) or 1

    if line_items:
        for r_idx, item in enumerate(line_items, 3):
            bg = LGREY if r_idx % 2 == 0 else WHITE
            ws.row_dimensions[r_idx].height = 18
            _data_cell(ws, r_idx, 1, item.get("name", ""), bg=bg, bold=False)
            _data_cell(ws, r_idx, 2, item.get("category", ""), bg=bg, align="center")
            vendor = item.get("vendor_origin", "")
            vendor_color = ORANGE if "donor" in vendor else (GREEN if vendor == "local" else YELLOW)
            _data_cell(ws, r_idx, 3, vendor, bg=vendor_color, fg=WHITE, align="center", bold=True)
            amt = item.get("amount_usd", 0)
            _data_cell(ws, r_idx, 4, amt, bg=bg, align="right", number_format='$#,##0')
            pct = amt / total_budget * 100
            _data_cell(ws, r_idx, 5, f"{pct:.1f}%", bg=bg, align="center")

        next_row = len(line_items) + 3
    else:
        # Reconstruct from risk metrics
        leakage = report["leakage"]
        rows_data = [
            ("Consultancy (estimated)", "consultancy", "mixed",
             total_budget * leakage["consultancy_pct"] / 100),
            ("Admin Overhead (estimated)", "administration", "mixed",
             total_budget * leakage["admin_overhead_pct"] / 100),
            ("Direct Delivery (estimated)", "direct_delivery", "local",
             total_budget * (100 - leakage["admin_overhead_pct"]) / 100),
        ]
        for r_idx, (name, cat, vendor, amt) in enumerate(rows_data, 3):
            bg = LGREY if r_idx % 2 == 0 else WHITE
            ws.row_dimensions[r_idx].height = 18
            _data_cell(ws, r_idx, 1, name, bg=bg)
            _data_cell(ws, r_idx, 2, cat, bg=bg, align="center")
            _data_cell(ws, r_idx, 3, vendor, bg=bg, align="center")
            _data_cell(ws, r_idx, 4, amt, bg=bg, align="right", number_format='$#,##0')
            pct = amt / total_budget * 100
            _data_cell(ws, r_idx, 5, f"{pct:.1f}%", bg=bg, align="center")
        next_row = 6

    # Totals row
    ws.row_dimensions[next_row].height = 22
    _header_cell(ws, next_row, 1, "TOTAL", bg=NAVY, colspan=3)
    _data_cell(ws, next_row, 4, total_budget, bg=GOLD, fg=WHITE, bold=True,
               align="right", number_format='$#,##0')
    _data_cell(ws, next_row, 5, "100.0%", bg=GOLD, fg=WHITE, bold=True, align="center")

    _set_col_widths(ws, {"A": 36, "B": 20, "C": 16, "D": 16, "E": 12})


def _build_scores_chart_sheet(wb: Workbook, report: dict):
    ws = wb.create_sheet("Score Charts")
    ws.sheet_view.showGridLines = False

    _header_cell(ws, 1, 1, "Integrity Score Dashboard", colspan=6, size=14)
    ws.row_dimensions[1].height = 32

    # Score data for chart
    _header_cell(ws, 3, 1, "Dimension", bg=LGREY, fg=NAVY, size=10)
    _header_cell(ws, 3, 2, "Score (/100)", bg=LGREY, fg=NAVY, size=10)
    _header_cell(ws, 3, 3, "Risk Level", bg=LGREY, fg=NAVY, size=10)

    score_data = [
        ("Overall", report.get("overall_score", 0), report.get("overall_risk", "")),
        ("Leakage Detection", report["leakage"]["integrity_score"], report["leakage"]["risk_level"]),
        ("Impact-to-Citizen", report["impact"]["integrity_score"], report["impact"]["risk_level"]),
        ("Sovereign Alignment", report["sovereign"]["integrity_score"], report["sovereign"]["risk_level"]),
    ]

    for r_idx, (dim, score, risk_lv) in enumerate(score_data, 4):
        ws.row_dimensions[r_idx].height = 20
        _data_cell(ws, r_idx, 1, dim, bg=LGREY if r_idx % 2 == 0 else WHITE, bold=(r_idx == 4))
        bar_color = _risk_bg(risk_lv)
        cell = ws.cell(row=r_idx, column=2, value=score)
        cell.font = _font(bold=True, color=WHITE)
        cell.fill = _fill(bar_color)
        cell.alignment = _align("center")
        cell.border = _border()
        _data_cell(ws, r_idx, 3, risk_lv, bg=bar_color, fg=WHITE, bold=True, align="center")

    # Bar chart
    chart = BarChart()
    chart.type = "col"
    chart.title = f"Integrity Scores — {report.get('project_name', '')}"
    chart.y_axis.title = "Score (0–100)"
    chart.y_axis.scaling.min = 0
    chart.y_axis.scaling.max = 100
    chart.shape = 4
    chart.style = 10
    chart.width = 20
    chart.height = 12

    data_ref = Reference(ws, min_col=2, min_row=3, max_row=7)
    cats_ref = Reference(ws, min_col=1, min_row=4, max_row=7)
    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cats_ref)

    # Color bars manually
    for i, (_, _, risk_lv) in enumerate(score_data):
        pt = DataPoint(idx=i)
        pt.graphicalProperties.solidFill = _risk_bg(risk_lv)
        chart.series[0].dPt.append(pt)

    ws.add_chart(chart, "E3")

    # Threshold reference line annotation
    _header_cell(ws, 10, 1, "Scoring Reference", bg=GOLD, size=10, colspan=3)
    thresholds = [
        ("90–100", "Excellent — no significant risk indicators"),
        ("70–89",  "Good — minor concerns, within acceptable bounds"),
        ("50–69",  "Caution — investigation warranted"),
        ("30–49",  "High Risk — intervention required"),
        ("0–29",   "Critical — project suspension recommended"),
    ]
    for r_idx, (range_str, desc) in enumerate(thresholds, 11):
        bg = LGREY if r_idx % 2 == 0 else WHITE
        ws.row_dimensions[r_idx].height = 16
        _data_cell(ws, r_idx, 1, range_str, bg=bg, bold=True, align="center")
        cell = ws.cell(row=r_idx, column=2, value=desc)
        cell.font = _font(size=9)
        cell.fill = _fill(bg)
        cell.alignment = _align("left", "center")
        cell.border = _border()
        ws.merge_cells(start_row=r_idx, start_column=2, end_row=r_idx, end_column=3)

    _set_col_widths(ws, {"A": 24, "B": 16, "C": 16})


# ── Donor comparison sheet ─────────────────────────────────────────────────────

def _build_donor_comparison_sheet(wb: Workbook, reports: list[dict]):
    ws = wb.create_sheet("Donor Comparison")
    ws.sheet_view.showGridLines = False

    _header_cell(ws, 1, 1, "Donor Risk Pattern Comparison — ADB vs World Bank vs USAID", colspan=8, size=13)
    ws.row_dimensions[1].height = 32

    # Group reports by donor
    donors = {}
    for r in reports:
        donor_raw = (r.get("donor") or r.get("project", {}).get("donor", "Unknown")).strip()
        # Normalize donor names
        if any(k in donor_raw.upper() for k in ["ADB", "ASIAN DEVELOPMENT"]):
            key = "ADB"
        elif any(k in donor_raw.upper() for k in ["WORLD BANK", "WB", "IBRD", "IDA"]):
            key = "World Bank"
        elif any(k in donor_raw.upper() for k in ["USAID", "US AID", "UNITED STATES AGENCY"]):
            key = "USAID"
        else:
            key = donor_raw[:20]
        donors.setdefault(key, []).append(r)

    if not donors:
        _data_cell(ws, 3, 1, "No data available — run analyses first.")
        return

    # Summary table header
    ws.row_dimensions[3].height = 22
    col_headers = ["Donor", "Projects", "Avg Score", "Avg Leakage Score",
                   "Avg Impact Score", "Avg Sovereign Score", "High/Critical %", "Common Risk Pattern"]
    for i, h in enumerate(col_headers, 1):
        _header_cell(ws, 3, i, h, size=9)

    # Donor-level aggregates
    donor_row = 4
    donor_chart_data = {}  # for chart

    for donor_name, donor_reports in donors.items():
        n = len(donor_reports)
        avg_score    = sum(r.get("overall_score", 0) for r in donor_reports) / n
        avg_leakage  = sum(r["leakage"]["integrity_score"] for r in donor_reports) / n
        avg_impact   = sum(r["impact"]["integrity_score"] for r in donor_reports) / n
        avg_sovereign = sum(r["sovereign"]["integrity_score"] for r in donor_reports) / n
        high_crit = sum(1 for r in donor_reports if r.get("overall_risk") in ("High", "Critical"))
        high_crit_pct = high_crit / n * 100

        # Determine common risk pattern
        leakage_issues   = sum(1 for r in donor_reports if r["leakage"]["risk_level"] in ("High", "Critical"))
        impact_issues    = sum(1 for r in donor_reports if r["impact"]["risk_level"] in ("High", "Critical"))
        sovereign_issues = sum(1 for r in donor_reports if r["sovereign"]["risk_level"] in ("High", "Critical"))
        max_issues = max(leakage_issues, impact_issues, sovereign_issues)
        if max_issues == 0:
            pattern = "✓ Low risk across all dimensions"
        elif max_issues == leakage_issues:
            pattern = "⚠ Leakage / procurement inflation"
        elif max_issues == sovereign_issues:
            pattern = "⚠ Sovereignty / conditionality issues"
        else:
            pattern = "⚠ Impact delivery gap"

        bg = LGREY if donor_row % 2 == 0 else WHITE
        ws.row_dimensions[donor_row].height = 20
        _data_cell(ws, donor_row, 1, donor_name, bg=bg, bold=True)
        _data_cell(ws, donor_row, 2, n, bg=bg, align="center")
        score_bg = _risk_bg("Low" if avg_score >= 70 else "Medium" if avg_score >= 50 else "High")
        _data_cell(ws, donor_row, 3, round(avg_score, 1), bg=score_bg, fg=WHITE, bold=True, align="center")
        _data_cell(ws, donor_row, 4, round(avg_leakage, 1), bg=bg, align="center")
        _data_cell(ws, donor_row, 5, round(avg_impact, 1), bg=bg, align="center")
        _data_cell(ws, donor_row, 6, round(avg_sovereign, 1), bg=bg, align="center")
        _data_cell(ws, donor_row, 7, f"{high_crit_pct:.0f}%", bg=bg, align="center")
        _data_cell(ws, donor_row, 8, pattern, bg=bg, align="left")

        donor_chart_data[donor_name] = {
            "Overall": round(avg_score, 1),
            "Leakage": round(avg_leakage, 1),
            "Impact": round(avg_impact, 1),
            "Sovereign": round(avg_sovereign, 1),
        }
        donor_row += 1

    # Chart data area for grouped bar chart
    chart_start_row = donor_row + 2
    _header_cell(ws, chart_start_row, 1, "Chart Data (Avg Scores by Donor)", bg=LGREY, fg=NAVY, colspan=5, size=9)
    chart_start_row += 1

    dims = ["Overall", "Leakage", "Impact", "Sovereign"]
    # Header row with donor names
    _data_cell(ws, chart_start_row, 1, "Dimension", bold=True)
    for ci, donor_name in enumerate(donor_chart_data.keys(), 2):
        _header_cell(ws, chart_start_row, ci, donor_name, bg=GOLD, size=9)

    for di, dim in enumerate(dims):
        row_n = chart_start_row + 1 + di
        ws.row_dimensions[row_n].height = 18
        _data_cell(ws, row_n, 1, dim, bold=True)
        for ci, scores in enumerate(donor_chart_data.values(), 2):
            _data_cell(ws, row_n, ci, scores[dim], align="center")

    # Build grouped bar chart
    if len(donor_chart_data) > 0:
        chart = BarChart()
        chart.type = "col"
        chart.grouping = "clustered"
        chart.title = "Average Integrity Scores by Donor"
        chart.y_axis.title = "Score (0–100)"
        chart.y_axis.scaling.min = 0
        chart.y_axis.scaling.max = 100
        chart.width = 22
        chart.height = 13
        chart.style = 10

        max_col = 1 + len(donor_chart_data)
        for ci in range(2, max_col + 1):
            ref = Reference(ws, min_col=ci, min_row=chart_start_row, max_row=chart_start_row + len(dims))
            chart.add_data(ref, titles_from_data=True)
        cats = Reference(ws, min_col=1, min_row=chart_start_row + 1, max_row=chart_start_row + len(dims))
        chart.set_categories(cats)
        ws.add_chart(chart, f"A{chart_start_row + 6}")

    # Project-level detail table
    detail_row = chart_start_row + 22
    _header_cell(ws, detail_row, 1, "Project-Level Detail by Donor", colspan=8, size=11)
    detail_row += 1
    detail_headers = ["Project", "Country", "Donor", "Overall Score", "Risk Level",
                      "Leakage Score", "Impact Score", "Sovereign Score"]
    for i, h in enumerate(detail_headers, 1):
        _header_cell(ws, detail_row, i, h, size=9)
    detail_row += 1

    for r in reports:
        bg = LGREY if detail_row % 2 == 0 else WHITE
        ws.row_dimensions[detail_row].height = 18
        _data_cell(ws, detail_row, 1, r.get("project_name", ""), bg=bg)
        _data_cell(ws, detail_row, 2, r.get("country", ""), bg=bg, align="center")
        _data_cell(ws, detail_row, 3, r.get("donor", ""), bg=bg)
        risk_lv = r.get("overall_risk", "")
        _data_cell(ws, detail_row, 4, r.get("overall_score", 0), bg=_risk_bg(risk_lv), fg=WHITE, bold=True, align="center")
        _data_cell(ws, detail_row, 5, risk_lv, bg=_risk_bg(risk_lv), fg=WHITE, bold=True, align="center")
        _data_cell(ws, detail_row, 6, r["leakage"]["integrity_score"], bg=bg, align="center")
        _data_cell(ws, detail_row, 7, r["impact"]["integrity_score"], bg=bg, align="center")
        _data_cell(ws, detail_row, 8, r["sovereign"]["integrity_score"], bg=bg, align="center")
        detail_row += 1

    _set_col_widths(ws, {"A": 28, "B": 12, "C": 14, "D": 13, "E": 14, "F": 14, "G": 14, "H": 28})


# ── Risk trend sheet ───────────────────────────────────────────────────────────

def _build_risk_trend_sheet(wb: Workbook, history_data: dict[str, list[dict]]):
    """
    history_data: {project_slug: [snapshot_dict, ...]}
    """
    ws = wb.create_sheet("Risk Trend Dashboard")
    ws.sheet_view.showGridLines = False

    _header_cell(ws, 1, 1, "Country-Level Risk Trend Dashboard", colspan=8, size=14)
    ws.row_dimensions[1].height = 32

    # Group snapshots by country → aggregate over time
    country_timeline: dict[str, dict[str, list]] = {}
    for slug, snapshots in history_data.items():
        for snap in snapshots:
            country = snap.get("country", "Unknown")
            ts = snap.get("generated_at", "")[:10]  # date part only
            score = snap.get("overall_score", 0)
            country_timeline.setdefault(country, {}).setdefault(ts, []).append(score)

    if not country_timeline:
        _data_cell(ws, 3, 1, "No historical data available yet.")
        return

    # Aggregate: average score per country per date
    country_avgs: dict[str, dict[str, float]] = {}
    all_dates = set()
    for country, date_scores in country_timeline.items():
        country_avgs[country] = {}
        for dt, scores in date_scores.items():
            country_avgs[country][dt] = round(sum(scores) / len(scores), 1)
            all_dates.add(dt)

    sorted_dates = sorted(all_dates)
    sorted_countries = sorted(country_avgs.keys())

    # Build data table for chart
    row = 3
    _header_cell(ws, row, 1, "Country / Date →", bg=NAVY, size=9)
    for ci, dt in enumerate(sorted_dates, 2):
        _header_cell(ws, row, ci, dt, bg=NAVY, size=9)
    ws.row_dimensions[row].height = 20
    row += 1

    for country in sorted_countries:
        bg = LGREY if row % 2 == 0 else WHITE
        ws.row_dimensions[row].height = 18
        _data_cell(ws, row, 1, country, bg=bg, bold=True)
        for ci, dt in enumerate(sorted_dates, 2):
            val = country_avgs[country].get(dt, None)
            if val is not None:
                score_bg = _risk_bg("Low" if val >= 70 else "Medium" if val >= 50 else "High" if val >= 30 else "Critical")
                _data_cell(ws, row, ci, val, bg=score_bg, fg=WHITE, bold=True, align="center")
            else:
                _data_cell(ws, row, ci, "—", bg=bg, fg=MGREY, align="center")
        row += 1

    # Summary stats
    row += 1
    _header_cell(ws, row, 1, "Country Summary", colspan=4, bg=GOLD, size=10)
    row += 1
    _header_cell(ws, row, 1, "Country", size=9)
    _header_cell(ws, row, 2, "Latest Score", size=9)
    _header_cell(ws, row, 3, "Trend", size=9)
    _header_cell(ws, row, 4, "# Snapshots", size=9)
    row += 1

    for country in sorted_countries:
        scores_by_date = country_avgs[country]
        sorted_country_dates = sorted(scores_by_date.keys())
        if not sorted_country_dates:
            continue
        latest = scores_by_date[sorted_country_dates[-1]]
        first  = scores_by_date[sorted_country_dates[0]]
        delta  = latest - first
        if len(sorted_country_dates) > 1:
            trend = f"↑ +{delta:.1f}" if delta > 1 else (f"↓ {delta:.1f}" if delta < -1 else "→ Stable")
        else:
            trend = "→ Single snapshot"
        n_snaps = sum(len(history_data[s]) for s in history_data
                      if any(snap.get("country") == country for snap in history_data[s]))
        bg = LGREY if row % 2 == 0 else WHITE
        ws.row_dimensions[row].height = 18
        _data_cell(ws, row, 1, country, bg=bg, bold=True)
        score_bg = _risk_bg("Low" if latest >= 70 else "Medium" if latest >= 50 else "High")
        _data_cell(ws, row, 2, latest, bg=score_bg, fg=WHITE, bold=True, align="center")
        trend_color = GREEN if delta > 0 else (RED if delta < 0 else MGREY)
        _data_cell(ws, row, 3, trend, fg=trend_color, bold=True, align="center")
        _data_cell(ws, row, 4, n_snaps, bg=bg, align="center")
        row += 1

    # Line chart for country trends (if enough data)
    if len(sorted_dates) >= 2:
        chart = LineChart()
        chart.title = "Country Risk Score Over Time"
        chart.y_axis.title = "Avg Integrity Score"
        chart.x_axis.title = "Date"
        chart.y_axis.scaling.min = 0
        chart.y_axis.scaling.max = 100
        chart.width = 22
        chart.height = 13
        chart.style = 10

        for ci, country in enumerate(sorted_countries):
            country_row_idx = 4 + ci
            data_ref = Reference(ws, min_col=2, max_col=1 + len(sorted_dates),
                                 min_row=country_row_idx, max_row=country_row_idx)
            chart.add_data(data_ref)
            from openpyxl.chart.series import SeriesLabel
            chart.series[-1].title = SeriesLabel(v=country)

        cats = Reference(ws, min_col=2, max_col=1 + len(sorted_dates), min_row=3)
        chart.set_categories(cats)
        ws.add_chart(chart, f"F3")

    _set_col_widths(ws, {"A": 22, "B": 13, "C": 13, "D": 13, "E": 13, "F": 14})


# ── Multi-language sheet ───────────────────────────────────────────────────────

def _build_multilang_sheet(wb: Workbook, report: dict):
    """Summary sheet in Arabic and French for francophone LDC coverage."""
    ws = wb.create_sheet("Multilingual Summary")
    ws.sheet_view.showGridLines = False

    _header_cell(ws, 1, 1, "Multilingual Summary — Résumé multilingue / ملخص متعدد اللغات", colspan=4, size=12)
    ws.row_dimensions[1].height = 30

    risk = report.get("overall_risk", "")
    score = report.get("overall_score", 0)
    project = report.get("project_name", "")
    country = report.get("country", "")

    risk_fr = {"Low": "Faible", "Medium": "Moyen", "High": "Élevé", "Critical": "Critique"}.get(risk, risk)
    risk_ar = {"Low": "منخفض", "Medium": "متوسط", "High": "مرتفع", "Critical": "حرج"}.get(risk, risk)

    # French section
    row = 3
    _header_cell(ws, row, 1, "🇫🇷 Résumé en Français (Couverture LDC Francophone)", colspan=4, bg=GOLD, size=11)
    row += 1

    fr_data = [
        ("Projet", project),
        ("Pays", country),
        ("Risque Global", risk_fr),
        ("Score d'Intégrité", f"{score}/100"),
        ("Score Fuites Financières", f"{report['leakage']['integrity_score']}/100"),
        ("Score Impact Citoyen", f"{report['impact']['integrity_score']}/100"),
        ("Score Alignement Souverain", f"{report['sovereign']['integrity_score']}/100"),
        ("Aide Liée Détectée", "Oui ⚠" if report["sovereign"]["tied_aid_detected"] else "Non ✓"),
        ("Risque Conditionnalité", "Oui ⚠" if report["sovereign"]["conditionality_risk"] else "Non ✓"),
    ]

    for label, value in fr_data:
        ws.row_dimensions[row].height = 18
        bg = LGREY if row % 2 == 0 else WHITE
        _data_cell(ws, row, 1, label, bg=bg, bold=True)
        is_risk_cell = label == "Risque Global"
        _data_cell(ws, row, 2, value,
                   bg=_risk_bg(risk) if is_risk_cell else bg,
                   fg=WHITE if is_risk_cell else None,
                   bold=is_risk_cell)
        row += 1

    row += 1

    # Arabic section (RTL visual note)
    _header_cell(ws, row, 1, "🇸🇦 الملخص بالعربية (التغطية: الدول الأقل نمواً)", colspan=4, bg=NAVY, size=11)
    row += 1

    ar_data = [
        ("المشروع", project),
        ("الدولة", country),
        ("مستوى المخاطر الإجمالي", risk_ar),
        ("نقاط النزاهة", f"{score}/100"),
        ("نقاط كشف التسرب المالي", f"{report['leakage']['integrity_score']}/100"),
        ("نقاط التأثير على المواطن", f"{report['impact']['integrity_score']}/100"),
        ("نقاط الانحياز السيادي", f"{report['sovereign']['integrity_score']}/100"),
        ("تم الكشف عن المساعدة المشروطة", "نعم ⚠" if report["sovereign"]["tied_aid_detected"] else "لا ✓"),
        ("خطر الاشتراطات", "نعم ⚠" if report["sovereign"]["conditionality_risk"] else "لا ✓"),
    ]

    for label, value in ar_data:
        ws.row_dimensions[row].height = 20
        bg = LGREY if row % 2 == 0 else WHITE
        # Arabic label in col B (right), value in col A (left) for RTL visual convention
        cell_label = ws.cell(row=row, column=2, value=label)
        cell_label.font = Font(name="Arial", size=10, bold=True, color="222222")
        cell_label.fill = _fill(bg)
        cell_label.alignment = Alignment(horizontal="right", vertical="center", readingOrder=2)
        cell_label.border = _border()

        is_risk_cell = label == "مستوى المخاطر الإجمالي"
        cell_val = ws.cell(row=row, column=1, value=value)
        cell_val.font = Font(name="Arial", size=10, bold=is_risk_cell, color=WHITE if is_risk_cell else "222222")
        cell_val.fill = _fill(_risk_bg(risk) if is_risk_cell else bg)
        cell_val.alignment = Alignment(horizontal="center", vertical="center", readingOrder=2)
        cell_val.border = _border()
        row += 1

    # French narrative
    row += 1
    _header_cell(ws, row, 1, "Résumé de la Société Civile (FR)", colspan=4, bg=GOLD, size=10)
    row += 1
    ws.row_dimensions[row].height = 60
    cell = ws.cell(row=row, column=1, value=report.get("civil_society_summary", ""))
    cell.font = _font(size=9, color=DGREY)
    cell.alignment = _align("left", "top", wrap=True)
    cell.fill = _fill(LGREY)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)

    _set_col_widths(ws, {"A": 30, "B": 30, "C": 16, "D": 14})


# ── Public API ─────────────────────────────────────────────────────────────────

def export_single_report(
    report: dict,
    output_path: Path,
    line_items: list = None,
    history_data: dict = None,
) -> Path:
    """
    Export a single project integrity report as a formatted .xlsx file.

    Args:
        report: IntegrityReport-like dict
        output_path: where to write the file
        line_items: optional raw line items list
        history_data: optional {slug: [snapshots]} for trend sheet
    """
    wb = Workbook()
    # Remove default sheet
    del wb["Sheet"]

    _build_summary_sheet(wb, report)
    _build_findings_sheet(wb, report)
    _build_budget_breakdown_sheet(wb, report, line_items)
    _build_scores_chart_sheet(wb, report)
    _build_multilang_sheet(wb, report)
    if history_data:
        _build_risk_trend_sheet(wb, history_data)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    return output_path


def export_donor_comparison(
    reports: list[dict],
    output_path: Path,
    history_data: dict = None,
) -> Path:
    """
    Export a multi-project donor comparison report as .xlsx.

    Args:
        reports: list of IntegrityReport-like dicts
        output_path: where to write the file
        history_data: optional {slug: [snapshots]} for trend sheet
    """
    wb = Workbook()
    del wb["Sheet"]

    _build_donor_comparison_sheet(wb, reports)
    if history_data:
        _build_risk_trend_sheet(wb, history_data)

    # Add individual summaries as sub-sheets
    for r in reports:
        slug = r.get("project_name", "Project")[:20]
        tab_name = slug.replace("/", "-").replace("\\", "-")[:31]
        _build_summary_sheet(wb, r)
        # Rename the last Summary sheet to the project name
        wb.worksheets[-1].title = tab_name

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    return output_path


def export_to_bytes(
    reports: list[dict],
    history_data: dict = None,
    mode: str = "single",
) -> bytes:
    """Return xlsx as bytes for Flask send_file."""
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        if mode == "comparison" or len(reports) > 1:
            export_donor_comparison(reports, tmp_path, history_data)
        else:
            export_single_report(reports[0], tmp_path, history_data=history_data)
        return tmp_path.read_bytes()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
