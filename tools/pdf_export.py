"""
PDF Report Exporter
Generates professional integrity reports in PDF format.
Uses ReportLab Platypus for layout.
Bengali (Bangla) text is rendered via a Unicode-aware font (NotoSansBengali).
Falls back gracefully if the font is not installed.
"""

import io
import os
import datetime
import subprocess
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage, PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from core.models import IntegrityReport, RiskLevel

# ── Bengali font registration ─────────────────────────────────────────────────

BENGALI_FONT_NAME = "NotoSansBengali"
BENGALI_FONT_AVAILABLE = False

_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
    "/usr/share/fonts/noto/NotoSansBengali-Regular.ttf",
    "/usr/local/share/fonts/NotoSansBengali-Regular.ttf",
    str(Path.home() / ".fonts/NotoSansBengali-Regular.ttf"),
]

for _fp in _FONT_PATHS:
    if Path(_fp).exists():
        try:
            pdfmetrics.registerFont(TTFont(BENGALI_FONT_NAME, _fp))
            BENGALI_FONT_AVAILABLE = True
            break
        except Exception:
            pass

if not BENGALI_FONT_AVAILABLE:
    # Try to install
    try:
        subprocess.run(
            ["apt-get", "install", "-y", "-q", "fonts-noto"],
            capture_output=True, check=True
        )
        for _fp in _FONT_PATHS:
            if Path(_fp).exists():
                pdfmetrics.registerFont(TTFont(BENGALI_FONT_NAME, _fp))
                BENGALI_FONT_AVAILABLE = True
                break
    except Exception:
        pass

# ── Colour palette ────────────────────────────────────────────────────────────

DARK_NAVY  = colors.HexColor("#0D1B2A")
GOLD       = colors.HexColor("#C9A84C")
LIGHT_GREY = colors.HexColor("#F5F5F5")
MID_GREY   = colors.HexColor("#CCCCCC")
WHITE      = colors.white

RISK_COLOR = {
    RiskLevel.LOW:      colors.HexColor("#27AE60"),
    RiskLevel.MEDIUM:   colors.HexColor("#F39C12"),
    RiskLevel.HIGH:     colors.HexColor("#E67E22"),
    RiskLevel.CRITICAL: colors.HexColor("#C0392B"),
}

# ── Style helpers ─────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    bn_font = BENGALI_FONT_NAME if BENGALI_FONT_AVAILABLE else "Helvetica"

    extra = {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"],
            fontName="Helvetica-Bold", fontSize=22,
            textColor=DARK_NAVY, spaceAfter=6, alignment=TA_CENTER,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"],
            fontName="Helvetica", fontSize=11,
            textColor=colors.HexColor("#555555"), alignment=TA_CENTER,
        ),
        "section_heading": ParagraphStyle(
            "section_heading", parent=base["Heading1"],
            fontName="Helvetica-Bold", fontSize=13,
            textColor=DARK_NAVY, spaceBefore=12, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontName="Helvetica", fontSize=10,
            leading=14, spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["Normal"],
            fontName="Helvetica", fontSize=10,
            leading=14, leftIndent=14, bulletIndent=0, spaceAfter=3,
        ),
        "label": ParagraphStyle(
            "label", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=9,
            textColor=colors.HexColor("#333333"),
        ),
        "bengali": ParagraphStyle(
            "bengali", parent=base["Normal"],
            fontName=bn_font, fontSize=11,
            leading=18, spaceAfter=6,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"],
            fontName="Helvetica", fontSize=8,
            textColor=colors.HexColor("#888888"), alignment=TA_CENTER,
        ),
    }
    for k, v in extra.items():
        base.add(v)
    return base


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _score_gauge_chart(scores: dict[str, int]) -> io.BytesIO:
    """Horizontal bar chart of integrity scores."""
    fig, ax = plt.subplots(figsize=(5.5, 2.2))
    labels = list(scores.keys())
    values = list(scores.values())
    bar_colors = ["#27AE60" if v >= 70 else "#E67E22" if v >= 50 else "#C0392B" for v in values]

    bars = ax.barh(labels, values, color=bar_colors, height=0.45, edgecolor="none")
    ax.set_xlim(0, 100)
    ax.axvline(x=70, color="#888888", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Integrity Score (0–100)", fontsize=8)
    ax.tick_params(labelsize=8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    for bar, val in zip(bars, values):
        ax.text(val + 1, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", ha="left", fontsize=8, fontweight="bold")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _budget_pie_chart(project_name: str, line_items) -> io.BytesIO:
    """Pie chart of budget by category."""
    cat_totals: dict[str, float] = {}
    for item in line_items:
        cat_totals[item.category] = cat_totals.get(item.category, 0) + item.amount_usd

    labels = list(cat_totals.keys())
    values = list(cat_totals.values())

    fig, ax = plt.subplots(figsize=(4, 3))
    wedge_colors = plt.cm.Set2.colors[:len(labels)]
    wedges, texts, autotexts = ax.pie(
        values, labels=None, autopct="%1.0f%%",
        colors=wedge_colors, startangle=140, pctdistance=0.8,
        wedgeprops={"edgecolor": "white", "linewidth": 1.2}
    )
    for at in autotexts:
        at.set_fontsize(7)
    ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.22),
              ncol=3, fontsize=7, frameon=False)
    ax.set_title("Budget by Category", fontsize=9, pad=10)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Bengali translations (static strings) ────────────────────────────────────

BN = {
    "title":        "বাজেট সততা প্রতিবেদন",
    "overall_risk": "সামগ্রিক ঝুঁকি",
    "overall_score": "সামগ্রিক স্কোর",
    "leakage":      "বাজেট ফাঁস বিশ্লেষণ",
    "impact":       "নাগরিক প্রভাব বিশ্লেষণ",
    "sovereign":    "জাতীয় সার্বভৌমত্ব পরীক্ষা",
    "findings":     "মূল তথ্য",
    "recommendation": "সুপারিশ",
    "civil_summary": "সুশীল সমাজ সারসংক্ষেপ",
    "generated":    "প্রতিবেদন তৈরির সময়",
    "risk_low":     "কম ঝুঁকি",
    "risk_medium":  "মাঝারি ঝুঁকি",
    "risk_high":    "উচ্চ ঝুঁকি",
    "risk_critical": "জরুরি ঝুঁকি",
    "yes":          "হ্যাঁ ⚠",
    "no":           "না ✓",
}

RISK_BN = {
    RiskLevel.LOW:      BN["risk_low"],
    RiskLevel.MEDIUM:   BN["risk_medium"],
    RiskLevel.HIGH:     BN["risk_high"],
    RiskLevel.CRITICAL: BN["risk_critical"],
}

# ── Main export function ───────────────────────────────────────────────────────

def export_pdf(
    report: IntegrityReport,
    output_path: str | Path,
    project=None,       # BudgetProject (optional, for pie chart)
    include_bengali: bool = True,
    bengali_summary: str = "",   # Bengali text for civil society summary
) -> Path:
    """
    Generate a PDF report and write it to output_path.
    Returns the Path of the written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    S = _styles()
    story = []

    # ── Cover block ──────────────────────────────────────────────────────────
    story.append(Spacer(1, 1.2 * cm))
    story.append(Paragraph("BUDGET INTEGRITY REPORT", S["cover_title"]))
    if include_bengali and BENGALI_FONT_AVAILABLE:
        story.append(Paragraph(BN["title"], S["bengali"]))
    story.append(Spacer(1, 0.3 * cm))

    meta_data = [
        ["Project",   report.project_name],
        ["Country",   report.country],
        ["Generated", report.generated_at],
    ]
    meta_table = Table(meta_data, colWidths=[4 * cm, 12 * cm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",    (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [LIGHT_GREY, WHITE]),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.4 * cm))

    # ── Overall risk banner ──────────────────────────────────────────────────
    risk_color = RISK_COLOR[report.overall_risk]
    banner = Table(
        [[
            Paragraph(f"{report.overall_risk.emoji()} Overall Risk: {report.overall_risk.value}",
                      ParagraphStyle("banner", fontName="Helvetica-Bold", fontSize=14,
                                     textColor=WHITE, alignment=TA_CENTER)),
            Paragraph(f"Score: {report.overall_score}/100",
                      ParagraphStyle("banner_score", fontName="Helvetica-Bold", fontSize=14,
                                     textColor=WHITE, alignment=TA_CENTER)),
        ]],
        colWidths=[9 * cm, 7 * cm],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), risk_color),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(banner)
    story.append(Spacer(1, 0.5 * cm))

    # ── Score gauge chart ────────────────────────────────────────────────────
    story.append(Paragraph("Integrity Score Summary", S["section_heading"]))
    gauge_buf = _score_gauge_chart({
        "Overall":           report.overall_score,
        "Leakage":           report.leakage.integrity_score,
        "Impact":            report.impact.integrity_score,
        "Sovereign":         report.sovereign.integrity_score,
    })
    gauge_img = RLImage(gauge_buf, width=13 * cm, height=5 * cm)
    story.append(gauge_img)
    story.append(Spacer(1, 0.3 * cm))

    # ── Budget pie chart (if project supplied) ───────────────────────────────
    if project and project.line_items:
        pie_buf = _budget_pie_chart(project.name, project.line_items)
        pie_img = RLImage(pie_buf, width=10 * cm, height=7.5 * cm)
        story.append(pie_img)
        story.append(Spacer(1, 0.3 * cm))

    # ── Section builder ──────────────────────────────────────────────────────
    def _section(title: str, bn_title: str, agent_result, extras: list):
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD))
        heading = f"{agent_result.risk_level.emoji()} {title}   [{agent_result.risk_level.value}]"
        story.append(Paragraph(heading, S["section_heading"]))
        if include_bengali and BENGALI_FONT_AVAILABLE:
            story.append(Paragraph(bn_title, S["bengali"]))

        # Extra key-value pairs
        for label, value in extras:
            story.append(Paragraph(f"<b>{label}:</b> {value}", S["body"]))

        # Score
        story.append(Paragraph(
            f"<b>Integrity Score:</b> {agent_result.integrity_score}/100", S["body"]
        ))

        # Findings
        story.append(Paragraph("<b>Findings:</b>", S["label"]))
        for finding in agent_result.findings:
            story.append(Paragraph(f"• {finding}", S["bullet"]))

        # Recommendation
        story.append(Paragraph(
            f"<b>Recommendation:</b> {agent_result.recommendation}", S["body"]
        ))
        story.append(Spacer(1, 0.3 * cm))

    # 1. Leakage
    _section(
        "1. LEAKAGE DETECTION", BN["leakage"], report.leakage,
        [
            ("Consultancy % of Budget",
             f"{report.leakage.consultancy_pct:.1f}%  (threshold: 20%)"),
            ("Admin Overhead", f"{report.leakage.admin_overhead_pct:.1f}%"),
        ],
    )

    # 2. Impact
    cpb = (f"USD {report.impact.cost_per_beneficiary_usd:,.0f}"
           if report.impact.cost_per_beneficiary_usd else "N/A")
    _section(
        "2. IMPACT-TO-CITIZEN RATIO", BN["impact"], report.impact,
        [("Cost per Beneficiary", cpb)],
    )

    # 3. Sovereign
    _section(
        "3. SOVEREIGN ALIGNMENT", BN["sovereign"], report.sovereign,
        [
            ("Tied Aid Detected",
             BN["yes"] if report.sovereign.tied_aid_detected else BN["no"]),
            ("Conditionality Risk",
             BN["yes"] if report.sovereign.conditionality_risk else BN["no"]),
            ("National Law Override",
             BN["yes"] if report.sovereign.national_law_override_detected else BN["no"]),
        ],
    )

    # ── Civil society summary ────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    story.append(Paragraph("Civil Society Summary", S["section_heading"]))
    if include_bengali and BENGALI_FONT_AVAILABLE:
        story.append(Paragraph(BN["civil_summary"], S["bengali"]))
    story.append(Paragraph(report.civil_society_summary, S["body"]))

    if include_bengali and bengali_summary and BENGALI_FONT_AVAILABLE:
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("বাংলা সারসংক্ষেপ:", S["label"]))
        story.append(Paragraph(bengali_summary, S["bengali"]))

    # ── Footer note ──────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.8 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"Generated by LDC Budget Integrity Monitor · Political Insights BD (PIBD) · {report.generated_at}",
        S["footer"],
    ))
    if not BENGALI_FONT_AVAILABLE and include_bengali:
        story.append(Paragraph(
            "Note: Bengali font (NotoSansBengali) not found — Bengali text omitted. "
            "Install fonts-noto to enable.",
            S["footer"],
        ))

    doc.build(story)
    return output_path
