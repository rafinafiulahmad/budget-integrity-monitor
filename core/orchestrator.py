"""
Orchestrator v2: runs all three analysis agents + Bengali translation agent,
auto-saves to history, and synthesizes the final integrity report.
"""

import datetime
from core.models import BudgetProject, IntegrityReport, RiskLevel
from core.claude_client import ClaudeClient
from agents.leakage_agent import LeakageAgent
from agents.impact_agent import ImpactAgent
from agents.sovereign_agent import SovereignAlignmentAgent
from agents.bengali_agent import BengaliTranslationAgent
from config.settings import Settings

SYNTHESIS_SYSTEM = """You are a senior governance advisor writing a plain-language
budget integrity summary for civil society organizations, journalists, and citizens
in Least Developed Countries. Write clearly, avoid jargon, and be direct about risks."""


def _aggregate_risk(scores, levels):
    avg_score = round(sum(scores) / len(scores))
    order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
    worst = max(levels, key=lambda r: order.index(r))
    return avg_score, worst


class BudgetOrchestrator:
    def __init__(self, settings):
        self._settings = settings
        self._client = ClaudeClient(settings)
        self._leakage_agent = LeakageAgent(self._client, settings)
        self._impact_agent = ImpactAgent(self._client)
        self._sovereign_agent = SovereignAlignmentAgent(self._client)
        self._bengali_agent = BengaliTranslationAgent(self._client)

    async def run_full_analysis(self, project, include_bengali=True):
        print("  [1/5] Leakage detection...")
        leakage = self._leakage_agent.analyze(project)
        print(f"        → {leakage.risk_level.emoji()} {leakage.risk_level.value} (score: {leakage.integrity_score})")

        print("  [2/5] Impact-to-citizen ratio...")
        impact = self._impact_agent.analyze(project)
        print(f"        → {impact.risk_level.emoji()} {impact.risk_level.value} (score: {impact.integrity_score})")

        print("  [3/5] Sovereign alignment check...")
        sovereign = self._sovereign_agent.analyze(project)
        print(f"        → {sovereign.risk_level.emoji()} {sovereign.risk_level.value} (score: {sovereign.integrity_score})")

        print("  [4/5] Synthesizing civil society summary...")
        summary = self._synthesize_summary(project, leakage, impact, sovereign)

        overall_score, overall_risk = _aggregate_risk(
            [leakage.integrity_score, impact.integrity_score, sovereign.integrity_score],
            [leakage.risk_level, impact.risk_level, sovereign.risk_level],
        )

        report = IntegrityReport(
            project_name=project.name,
            country=project.country,
            generated_at=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            overall_risk=overall_risk,
            overall_score=overall_score,
            leakage=leakage,
            impact=impact,
            sovereign=sovereign,
            civil_society_summary=summary,
        )

        if include_bengali:
            print("  [5/5] Generating Bengali translation...")
            try:
                bengali_text = self._bengali_agent.translate_summary(report)
                report.raw_llm_responses["bengali_summary"] = bengali_text
                print(f"        → Bengali summary generated ({len(bengali_text)} chars)")
            except Exception as e:
                print(f"        → Bengali translation failed: {e}")
        else:
            print("  [5/5] Bengali translation skipped.")

        return report

    def _synthesize_summary(self, project, leakage, impact, sovereign):
        prompt = f"""Write a 4-sentence plain-language summary of this budget integrity analysis
for a civil society newspaper in {project.country}.

Project: {project.name} (USD {project.total_budget_usd:,.0f}, donor: {project.donor})

Key findings:
- Leakage risk: {leakage.risk_level.value} | Consultancy at {leakage.consultancy_pct:.1f}% of budget
- Impact risk: {impact.risk_level.value} | Cost per beneficiary: USD {impact.cost_per_beneficiary_usd or 'unknown'}
- Sovereignty risk: {sovereign.risk_level.value} | Tied aid: {sovereign.tied_aid_detected} | Conditionality: {sovereign.conditionality_risk}

Top findings:
{chr(10).join('- ' + f for f in leakage.findings[:1] + impact.findings[:1] + sovereign.findings[:1])}

Write for citizens, not economists. Be direct. Sentences 1-2: what the project is and the main risk.
Sentence 3: the most critical specific finding. Sentence 4: what citizens should demand."""

        return self._client.complete(prompt, system_prompt=SYNTHESIS_SYSTEM)
