"""
Agent 1: Leakage Detector
Analyzes consultancy fees, admin overhead, and procurement inflation.
"""

from core.models import BudgetProject, LeakageResult, RiskLevel
from core.claude_client import ClaudeClient
from config.settings import Settings

SYSTEM_PROMPT = """You are a forensic public finance auditor specializing in
development projects in Least Developed Countries. You identify budget leakage
patterns: inflated consultancy fees, phantom administration costs, and
procurement corruption indicators.

Your analysis must be evidence-based, cite specific line items and percentages,
and reference international benchmarks (OECD, PEFA, IMF norms).

Prioritize accuracy over diplomacy. If something looks like leakage, say so clearly."""


class LeakageAgent:
    """
    Detects financial leakage in project budgets.

    Checks:
    - Consultancy fees > 20% of total budget → High Sovereign Risk
    - Admin overhead as % of total
    - Unusual concentration of funds in non-delivery line items
    """

    def __init__(self, client: ClaudeClient, settings: Settings):
        self._client = client
        self._threshold = settings.consultancy_risk_threshold_pct

    def analyze(self, project: BudgetProject) -> LeakageResult:
        consultancy_pct = (project.consultancy_total() / project.total_budget_usd) * 100
        admin_pct = (project.admin_total() / project.total_budget_usd) * 100

        prompt = f"""Analyze this LDC project budget for financial leakage.

{project.to_analysis_text()}

Pre-calculated metrics:
- Consultancy as % of total: {consultancy_pct:.1f}% (risk threshold: {self._threshold}%)
- Admin overhead as % of total: {admin_pct:.1f}%

Return JSON with this exact structure:
{{
  "risk_level": "Low|Medium|High|Critical",
  "findings": ["finding 1", "finding 2", "finding 3"],
  "recommendation": "single actionable recommendation string",
  "integrity_score": <integer 0-100>
}}

Scoring guide for integrity_score:
- 90-100: No leakage indicators, all spending appropriate
- 70-89: Minor concerns, within acceptable bounds
- 50-69: Significant leakage indicators, investigation warranted
- 30-49: High leakage probability, intervention required
- 0-29: Critical leakage, project should be suspended pending audit"""

        result = self._client.complete_json(prompt, system_prompt=SYSTEM_PROMPT)

        return LeakageResult(
            risk_level=RiskLevel(result["risk_level"]),
            consultancy_pct=round(consultancy_pct, 1),
            admin_overhead_pct=round(admin_pct, 1),
            findings=result["findings"],
            recommendation=result["recommendation"],
            integrity_score=int(result["integrity_score"]),
        )
