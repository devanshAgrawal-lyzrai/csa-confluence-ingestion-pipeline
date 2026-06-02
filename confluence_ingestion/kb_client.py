import logging
import time

import requests

logger = logging.getLogger(__name__)


class KBClient:
    """
    Client for the two Lyzr KB APIs:

    1. parse_text()  → POST /v3/parse/text/
       Splits a plain-text document into overlapping chunks.
       Returns the raw chunk array (all chunks share the same source value).

    2. train()       → POST /v3/rag/train/{kb_id}/
       Accepts the prepared chunk array (unique source UUIDs + extra_info filled)
       and stores embeddings in the vector DB.
    """

    def __init__(self, base_url: str, kb_id: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.kb_id = kb_id
        self.api_key = api_key
        self._headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

    # ------------------------------------------------------------------
    # Step 1: parse plain text into chunks
    # ------------------------------------------------------------------

    def parse_text(
        self,
        text: str,
        source: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
    ) -> list:
        """
        POST /v3/parse/text/

        Returns a list of raw chunk dicts:
          [{"text": "...", "source": "<same source>", "extra_info": {}}, ...]

        The caller is responsible for assigning unique source UUIDs and
        filling extra_info before calling train().
        """
        url = f"{self.base_url}/v3/parse/text/"
        payload = {
            "data": [{"text": text, "source": source, "extra_info": {}}],
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        }
        response = self._post_with_retry(url, payload)

        # Response may be a bare list or wrapped in {"data": [...]}
        if isinstance(response, list):
            chunks = response
        elif isinstance(response, dict):
            chunks = response.get("data", response.get("chunks", []))
        else:
            logger.warning("Unexpected parse response type: %s", type(response))
            chunks = []

        logger.debug("parse_text: %d chunks returned for source=%s", len(chunks), source)
        return chunks

    # ------------------------------------------------------------------
    # Step 2: train — store prepared chunks in the vector DB
    # ------------------------------------------------------------------

    def train(self, chunks: list) -> None:
        """
        POST /v3/rag/train/{kb_id}/

        chunks must already have unique source UUIDs and extra_info filled.
        Lyzr handles embedding + storage.
        """
        url = f"{self.base_url}/v3/rag/train/{self.kb_id}/"
        self._post_with_retry(url, chunks)
        logger.debug("train: %d chunks sent to KB %s", len(chunks), self.kb_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_with_retry(self, url: str, payload) -> any:
        for attempt in range(3):
            try:
                resp = requests.post(
                    url, headers=self._headers, json=payload, timeout=120
                )
                if resp.status_code in (429, 500, 502, 503):
                    wait = 2 ** attempt
                    logger.warning(
                        "HTTP %s from %s, retrying in %ss", resp.status_code, url, wait
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                try:
                    return resp.json()
                except Exception:
                    return {}
            except requests.exceptions.RequestException as exc:
                if attempt == 2:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "KB request error (%s), retrying in %ss: %s",
                    type(exc).__name__, wait, exc,
                )
                time.sleep(wait)
        raise RuntimeError(f"All retries exhausted for {url}")
