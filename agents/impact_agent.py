"""
Agent 2: Impact-to-Citizen Ratio Analyzer
Evaluates cost-per-beneficiary and alignment with human development indicators.
"""

from typing import Optional
from core.models import BudgetProject, ImpactResult, RiskLevel
from core.claude_client import ClaudeClient

SYSTEM_PROMPT = """You are a development economist specializing in human development
impact assessment for LDC projects. You cross-reference budget allocations against
UNDP Human Development Index components, WHO benchmarks, UNESCO education norms,
and World Bank infrastructure cost standards.

Your job is to determine whether money is reaching citizens or being absorbed by
overhead before delivery. Reference regional cost benchmarks (South Asia, Sub-Saharan
Africa, etc.) appropriate for the country being analyzed."""


class ImpactAgent:
    """
    Measures cost-per-beneficiary and development impact alignment.

    Checks:
    - Cost per direct beneficiary vs regional benchmarks
    - % of budget reaching delivery vs overhead
    - HDI sub-index alignment (health, education, income)
    """

    def __init__(self, client: ClaudeClient):
        self._client = client

    def analyze(self, project: BudgetProject) -> ImpactResult:
        cost_per_beneficiary: Optional[float] = None
        if project.beneficiary_count and project.beneficiary_count > 0:
            cost_per_beneficiary = project.total_budget_usd / project.beneficiary_count

        prompt = f"""Analyze the impact-to-citizen ratio for this LDC project.

{project.to_analysis_text()}

{f"Pre-calculated cost per beneficiary: USD {cost_per_beneficiary:,.0f}" if cost_per_beneficiary else "Beneficiary count not provided — estimate from sector norms."}

Evaluate:
1. Is the cost-per-beneficiary reasonable for this sector and region?
2. What % of the budget actually reaches service delivery vs overhead?
3. Which HDI sub-indicators (health, education, standard of living) does this budget support, and is the allocation proportionate?

Return JSON with this exact structure:
{{
  "risk_level": "Low|Medium|High|Critical",
  "cost_per_beneficiary_usd": <number or null>,
  "hdx_alignment_score": <integer 0-100, how well budget aligns with HDI improvement>,
  "findings": ["finding 1", "finding 2", "finding 3"],
  "recommendation": "single actionable recommendation string",
  "integrity_score": <integer 0-100>
}}

For integrity_score:
- 80-100: Strong delivery focus, realistic beneficiary costs
- 60-79: Adequate impact potential with some inefficiencies
- 40-59: Significant delivery gap, overhead consuming project value
- 0-39: Budget unlikely to achieve stated human development outcomes"""

        result = self._client.complete_json(prompt, system_prompt=SYSTEM_PROMPT)

        # Use pre-calculated value if model returns null
        cpb = result.get("cost_per_beneficiary_usd") or cost_per_beneficiary

        return ImpactResult(
            risk_level=RiskLevel(result["risk_level"]),
            cost_per_beneficiary_usd=round(cpb, 2) if cpb else None,
            hdx_alignment_score=result.get("hdx_alignment_score"),
            findings=result["findings"],
            recommendation=result["recommendation"],
            integrity_score=int(result["integrity_score"]),
        )
