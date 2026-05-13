"""
Agent 3: Sovereign Alignment Filter
Checks national law compliance vs donor conditionality.
"""

from core.models import BudgetProject, SovereignAlignmentResult, RiskLevel
from core.claude_client import ClaudeClient

SYSTEM_PROMPT = """You are a public procurement law specialist and development
finance expert focusing on sovereignty and conditionality in LDC aid projects.

You analyze whether donor conditions override national legal frameworks,
detect tied-aid arrangements that restrict procurement to donor-country vendors,
and flag conditionality clauses that undermine host-country ownership.

Key legal frameworks you know: PPR 2008 (Bangladesh), Nepal Public Procurement Act 2063,
Mali Law 08-023, UNCITRAL Model Law on Procurement, OECD DAC Untying Recommendations,
Paris Declaration on Aid Effectiveness (2005), Accra Agenda for Action (2008).

Rule: National procurement laws take precedence over donor "suggested" vendor lists
unless there is an explicit treaty override with parliamentary ratification."""


class SovereignAlignmentAgent:
    """
    Checks if budget respects national sovereignty and local laws.

    Checks:
    - Tied aid: procurement restricted to donor-country vendors
    - Conditionality: donor clauses overriding national law
    - Market distortion: foreign vendor concentration
    """

    def __init__(self, client: ClaudeClient):
        self._client = client

    def analyze(self, project: BudgetProject) -> SovereignAlignmentResult:
        foreign_pct = (project.foreign_vendor_total() / project.total_budget_usd) * 100

        prompt = f"""Analyze sovereign alignment for this LDC project budget.

{project.to_analysis_text()}

Pre-calculated: Foreign/donor-mandated vendor spend = {foreign_pct:.1f}% of total budget

Evaluate:
1. TIED AID: Is procurement restricted to donor-country or donor-approved vendors?
2. CONDITIONALITY: Do donor conditions override national procurement law?
3. NATIONAL LAW: Does the procurement method comply with the country's public procurement regulations?
4. MARKET DISTORTION: Is local industry/supply chain being bypassed?

Prioritize national procurement laws over external donor "suggested" vendors.
Flag any clause where donor conditions override national legal frameworks as CONDITIONALITY RISK.

Return JSON with this exact structure:
{{
  "risk_level": "Low|Medium|High|Critical",
  "tied_aid_detected": true|false,
  "conditionality_risk": true|false,
  "national_law_override_detected": true|false,
  "findings": ["finding 1", "finding 2", "finding 3"],
  "recommendation": "single actionable recommendation string",
  "integrity_score": <integer 0-100>
}}

For integrity_score:
- 80-100: Full national law compliance, no tied aid
- 60-79: Minor conditionality concerns, mostly compliant
- 40-59: Significant sovereignty concerns, partial compliance
- 0-39: Serious violations of national law or tied aid detected"""

        result = self._client.complete_json(prompt, system_prompt=SYSTEM_PROMPT)

        return SovereignAlignmentResult(
            risk_level=RiskLevel(result["risk_level"]),
            tied_aid_detected=bool(result["tied_aid_detected"]),
            conditionality_risk=bool(result["conditionality_risk"]),
            national_law_override_detected=bool(result["national_law_override_detected"]),
            findings=result["findings"],
            recommendation=result["recommendation"],
            integrity_score=int(result["integrity_score"]),
        )
