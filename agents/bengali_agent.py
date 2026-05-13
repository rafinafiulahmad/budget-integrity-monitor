"""
Bengali Translation Agent
Translates the civil society summary into Bengali (Bangla) using the Claude API.
Designed for reports targeting Bangladeshi citizens and media.
"""

from core.models import IntegrityReport
from core.claude_client import ClaudeClient

SYSTEM_PROMPT = """You are a skilled Bengali (Bangla) language expert and civic journalist.
Translate the provided budget integrity summary into clear, accessible Bengali.
Use standard modern Bengali script. Avoid jargon. Write for ordinary citizens and local journalists.
Maintain the factual accuracy of all numbers and risk levels.
Return ONLY the Bengali translation — no English, no preamble, no explanation."""


class BengaliTranslationAgent:
    """
    Translates civil society summaries into Bengali.
    Used for PDF reports and web UI Bengali output.
    """

    def __init__(self, client: ClaudeClient):
        self._client = client

    def translate_summary(self, report: IntegrityReport) -> str:
        """
        Translate the civil society summary portion of a report into Bengali.
        Returns the Bengali text string.
        """
        prompt = f"""Translate the following budget integrity summary into Bengali (Bangla).
This is for a civic newspaper audience in Bangladesh.

Project: {report.project_name}
Country: {report.country}
Overall Risk: {report.overall_risk.value}
Overall Score: {report.overall_score}/100

English summary to translate:
{report.civil_society_summary}

Additional context for accuracy:
- Consultancy: {report.leakage.consultancy_pct:.1f}% of budget
- Tied aid detected: {report.sovereign.tied_aid_detected}
- Conditionality risk: {report.sovereign.conditionality_risk}

Translate to Bengali now:"""

        return self._client.complete(prompt, system_prompt=SYSTEM_PROMPT)

    def translate_findings(self, findings: list[str]) -> list[str]:
        """Translate a list of finding strings into Bengali."""
        combined = "\n".join(f"{i+1}. {f}" for i, f in enumerate(findings))
        prompt = f"""Translate each of these numbered findings into Bengali.
Return ONLY the translated numbered list, nothing else.

{combined}"""
        result = self._client.complete(prompt, system_prompt=SYSTEM_PROMPT)
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        # Strip leading numbers/dots if present
        cleaned = []
        for line in lines:
            parts = line.split(".", 1)
            cleaned.append(parts[1].strip() if len(parts) == 2 and parts[0].strip().isdigit() else line)
        return cleaned or findings   # fallback to original on parse failure
