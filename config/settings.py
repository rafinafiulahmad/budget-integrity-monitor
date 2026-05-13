"""
Configuration and settings management.
Reads from environment variables or .env file.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class Settings:
    # Anthropic API
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-sonnet-4-20250514"))
    max_tokens: int = 2048

    # Analysis thresholds
    consultancy_risk_threshold_pct: float = 20.0   # flag if > 20% of total budget
    admin_overhead_risk_threshold_pct: float = 15.0
    cost_per_beneficiary_warning_multiplier: float = 1.4  # 40% above regional median

    # Paths
    reports_dir: Path = Path("reports")
    data_dir: Path = Path("data")

    # Rate limiting
    requests_per_minute: int = 10
    retry_attempts: int = 3
    retry_delay_seconds: float = 2.0

    def validate(self):
        if not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. "
                "Export it: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        return self
