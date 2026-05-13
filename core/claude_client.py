"""
Anthropic Claude API client.
Handles auth, retries, rate limiting, and response parsing.
"""

import asyncio
import time
import json
import re
from typing import Optional
import anthropic

from config.settings import Settings


class ClaudeClient:
    """
    Thin wrapper around the Anthropic SDK.
    Adds retry logic, rate limiting, and JSON extraction utilities.
    """

    def __init__(self, settings: Settings):
        settings.validate()
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.claude_model
        self._max_tokens = settings.max_tokens
        self._retry_attempts = settings.retry_attempts
        self._retry_delay = settings.retry_delay_seconds
        self._rpm = settings.requests_per_minute
        self._last_call_time: float = 0.0

    def _rate_limit(self):
        """Enforce requests-per-minute limit with simple token bucket."""
        min_gap = 60.0 / self._rpm
        elapsed = time.time() - self._last_call_time
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self._last_call_time = time.time()

    def complete(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.1,   # low temp for analytical tasks
    ) -> str:
        """
        Synchronous completion with retry logic.
        Returns the raw text response.
        """
        messages = [{"role": "user", "content": user_prompt}]
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        for attempt in range(self._retry_attempts):
            try:
                self._rate_limit()
                response = self._client.messages.create(**kwargs)
                return response.content[0].text
            except anthropic.RateLimitError:
                wait = self._retry_delay * (2 ** attempt)
                print(f"  [rate limit] waiting {wait:.1f}s before retry {attempt+1}...")
                time.sleep(wait)
            except anthropic.APIError as e:
                if attempt == self._retry_attempts - 1:
                    raise
                print(f"  [api error] {e}, retrying...")
                time.sleep(self._retry_delay)

        raise RuntimeError("Claude API failed after all retries")

    def complete_json(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> dict:
        """
        Request a JSON response and parse it safely.
        Strips markdown fences if the model adds them.
        """
        full_system = (system_prompt + "\n\n" if system_prompt else "") + (
            "CRITICAL: Respond ONLY with valid JSON. "
            "No preamble, no explanation, no markdown fences. "
            "Start your response with { and end with }."
        )
        raw = self.complete(user_prompt, system_prompt=full_system)
        return self._extract_json(raw)

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON from text, handling markdown code blocks."""
        # Strip markdown fences
        text = re.sub(r"```(?:json)?", "", text).strip()
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Find first { ... } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"No valid JSON found in Claude response:\n{text[:300]}")
