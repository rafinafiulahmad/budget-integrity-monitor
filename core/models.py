"""
Core data models for budget projects and analysis reports.
Using dataclasses for lightweight, type-safe structures.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum
import json
import datetime


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"

    def emoji(self) -> str:
        return {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🟠",
            RiskLevel.CRITICAL: "🔴",
        }[self]


@dataclass
class BudgetLineItem:
    name: str
    amount_usd: float
    category: str          # e.g. "consultancy", "civil_works", "equipment"
    vendor_origin: str = "local"   # "local" | "foreign" | "donor_mandated"
    notes: str = ""


@dataclass
class BudgetProject:
    name: str
    country: str
    total_budget_usd: float
    donor: str
    sector: str
    line_items: list[BudgetLineItem]
    procurement_method: str = ""
    beneficiary_count: Optional[int] = None
    national_law_reference: str = ""
    context_notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> BudgetProject:
        items = [BudgetLineItem(**i) for i in d.get("line_items", [])]
        return cls(
            name=d["name"],
            country=d["country"],
            total_budget_usd=float(d["total_budget_usd"]),
            donor=d["donor"],
            sector=d["sector"],
            line_items=items,
            procurement_method=d.get("procurement_method", ""),
            beneficiary_count=d.get("beneficiary_count"),
            national_law_reference=d.get("national_law_reference", ""),
            context_notes=d.get("context_notes", ""),
        )

    def consultancy_total(self) -> float:
        return sum(i.amount_usd for i in self.line_items if i.category == "consultancy")

    def admin_total(self) -> float:
        return sum(i.amount_usd for i in self.line_items
                   if i.category in ("consultancy", "administration", "project_management"))

    def foreign_vendor_total(self) -> float:
        return sum(i.amount_usd for i in self.line_items
                   if i.vendor_origin in ("foreign", "donor_mandated"))

    def to_analysis_text(self) -> str:
        lines = "\n".join(
            f"  - {i.name}: USD {i.amount_usd:,.0f} [{i.category}] vendor={i.vendor_origin}"
            for i in self.line_items
        )
        return f"""Project: {self.name}
Country: {self.country}
Total Budget: USD {self.total_budget_usd:,.0f}
Donor: {self.donor}
Sector: {self.sector}
Procurement: {self.procurement_method}
Beneficiaries: {self.beneficiary_count or 'Not specified'}
National Law: {self.national_law_reference or 'Not specified'}
Context: {self.context_notes or 'None'}
Line Items:
{lines}"""


# ── Analysis result models ────────────────────────────────────────────────────

@dataclass
class LeakageResult:
    risk_level: RiskLevel
    consultancy_pct: float
    admin_overhead_pct: float
    findings: list[str]
    recommendation: str
    integrity_score: int   # 0-100


@dataclass
class ImpactResult:
    risk_level: RiskLevel
    cost_per_beneficiary_usd: Optional[float]
    hdx_alignment_score: Optional[int]   # 0-100 alignment with HDI indicators
    findings: list[str]
    recommendation: str
    integrity_score: int


@dataclass
class SovereignAlignmentResult:
    risk_level: RiskLevel
    tied_aid_detected: bool
    conditionality_risk: bool
    national_law_override_detected: bool
    findings: list[str]
    recommendation: str
    integrity_score: int


@dataclass
class IntegrityReport:
    project_name: str
    country: str
    generated_at: str
    overall_risk: RiskLevel
    overall_score: int
    leakage: LeakageResult
    impact: ImpactResult
    sovereign: SovereignAlignmentResult
    civil_society_summary: str
    raw_llm_responses: dict = field(default_factory=dict)

    def to_json(self) -> str:
        def default(o):
            if isinstance(o, RiskLevel):
                return o.value
            if isinstance(o, datetime.datetime):
                return o.isoformat()
            return str(o)
        return json.dumps(asdict(self), indent=2, default=default)

    def to_text(self) -> str:
        r = self
        lines = [
            f"BUDGET INTEGRITY REPORT",
            f"{'='*60}",
            f"Project : {r.project_name}",
            f"Country : {r.country}",
            f"Generated: {r.generated_at}",
            f"",
            f"OVERALL RISK  : {r.overall_risk.emoji()} {r.overall_risk.value}",
            f"OVERALL SCORE : {r.overall_score}/100",
            f"",
            f"── 1. LEAKAGE DETECTION {r.leakage.risk_level.emoji()} {r.leakage.risk_level.value} ──",
            f"   Consultancy %   : {r.leakage.consultancy_pct:.1f}% (threshold: 20%)",
            f"   Admin Overhead  : {r.leakage.admin_overhead_pct:.1f}%",
            f"   Integrity Score : {r.leakage.integrity_score}/100",
            f"   Findings:",
        ]
        for f in r.leakage.findings:
            lines.append(f"     • {f}")
        lines += [
            f"   Recommendation: {r.leakage.recommendation}",
            f"",
            f"── 2. IMPACT-TO-CITIZEN RATIO {r.impact.risk_level.emoji()} {r.impact.risk_level.value} ──",
        ]
        if r.impact.cost_per_beneficiary_usd:
            lines.append(f"   Cost/Beneficiary: USD {r.impact.cost_per_beneficiary_usd:,.0f}")
        lines += [
            f"   Integrity Score : {r.impact.integrity_score}/100",
            f"   Findings:",
        ]
        for f in r.impact.findings:
            lines.append(f"     • {f}")
        lines += [
            f"   Recommendation: {r.impact.recommendation}",
            f"",
            f"── 3. SOVEREIGN ALIGNMENT {r.sovereign.risk_level.emoji()} {r.sovereign.risk_level.value} ──",
            f"   Tied Aid Detected    : {'Yes ⚠' if r.sovereign.tied_aid_detected else 'No ✓'}",
            f"   Conditionality Risk  : {'Yes ⚠' if r.sovereign.conditionality_risk else 'No ✓'}",
            f"   Nat. Law Override    : {'Yes ⚠' if r.sovereign.national_law_override_detected else 'No ✓'}",
            f"   Integrity Score      : {r.sovereign.integrity_score}/100",
            f"   Findings:",
        ]
        for f in r.sovereign.findings:
            lines.append(f"     • {f}")
        lines += [
            f"   Recommendation: {r.sovereign.recommendation}",
            f"",
            f"── CIVIL SOCIETY SUMMARY ──",
            r.civil_society_summary,
            f"{'='*60}",
        ]
        return "\n".join(lines)
