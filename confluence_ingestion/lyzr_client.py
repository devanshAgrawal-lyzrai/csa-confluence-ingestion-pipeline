import json
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

AGENT_ENDPOINT = "https://agent-prod.studio.lyzr.ai/v3/inference/chat/"

_PROMPT_TEMPLATE = """\
You are a knowledge base intelligence assistant for a benefits HR platform.

You will receive the plain-text content of a Confluence page.

Your task — analyze the page and return a JSON object with exactly these three fields:

1. "summary"         — 2-4 sentence plain-English summary of what this page covers.
2. "tags"            — list of 3-8 relevant topic tags (lowercase, e.g. "eligibility", \
"healthcare", "claims", "dental", "vision", "fsa", "hsa", "cobra", "enrollment").
3. "relevancy_score" — float 0.0–1.0 indicating how relevant this page is to a \
benefits/HR knowledge base (1.0 = highly relevant, 0.0 = not relevant at all).

Return ONLY a valid JSON object. No markdown fences, no explanation, no preamble.
Your entire response must be parseable by json.loads().

Example output:
{{
  "summary": "This page explains eligibility rules for the medical health care FSA. Employees become eligible after 90 days of employment. Coverage includes dental, vision, and prescription expenses.",
  "tags": ["eligibility", "healthcare", "fsa", "dental", "vision"],
  "relevancy_score": 0.97
}}

Page content:
{plain_text}
"""


class LyzrParseError(Exception):
    pass


class LyzrClient:
    def __init__(self, api_key: str, agent_id: str, user_id: str):
        self.api_key = api_key
        self.agent_id = agent_id
        self.user_id = user_id

    def get_page_intelligence(self, plain_text: str, session_id: str) -> dict:
        """
        Send the page's plain text to the agent.
        Returns: {"summary": str, "tags": [str], "relevancy_score": float}

        Configure your Lyzr agent's system prompt as:
            "You are a knowledge base intelligence assistant for a benefits HR platform.
             Always respond with valid JSON only. Never include markdown fences,
             preamble, or explanation. Your entire response must be parseable by json.loads()."
        """
        message = _PROMPT_TEMPLATE.format(plain_text=plain_text)
        payload = {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": session_id,
            "message": message,
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }
        response_text = self._post_with_retry(AGENT_ENDPOINT, headers, payload)
        return self._parse_agent_response(response_text)

    def _post_with_retry(self, url: str, headers: dict, payload: dict) -> str:
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                if resp.status_code in (429, 500, 502, 503):
                    wait = 2 ** attempt
                    logger.warning(
                        "HTTP %s from Lyzr agent, retrying in %ss", resp.status_code, wait
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", data.get("message", str(data)))
            except requests.exceptions.RequestException as exc:
                if attempt == 2:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Lyzr agent request error (%s), retrying in %ss",
                    type(exc).__name__, wait,
                )
                time.sleep(wait)
        raise RuntimeError("All retries exhausted for Lyzr agent call")

    def _parse_agent_response(self, response_text: str) -> dict:
        text = response_text.strip()

        fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "Lyzr agent returned non-JSON response: %s", response_text[:500]
            )
            raise LyzrParseError(f"Agent response is not valid JSON: {exc}") from exc

        # Validate expected keys; fall back gracefully if partial
        result = {
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
            "relevancy_score": float(data.get("relevancy_score", 0.0)),
        }

        if not result["summary"]:
            logger.warning("Agent response missing 'summary'. Raw keys: %s", list(data.keys()))
        if not result["tags"]:
            logger.warning("Agent response missing 'tags'. Raw keys: %s", list(data.keys()))

        return result
