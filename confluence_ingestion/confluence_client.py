import logging
import time
from typing import Iterator

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class ConfluenceClient:
    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(email, api_token)
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code in (429, 500, 502, 503):
                    wait = 2 ** attempt
                    logger.warning("HTTP %s from %s, retrying in %ss", resp.status_code, url, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as exc:
                if attempt == 2:
                    raise
                wait = 2 ** attempt
                logger.warning("Request error (%s), retrying in %ss: %s", type(exc).__name__, wait, exc)
                time.sleep(wait)
        raise RuntimeError(f"All retries exhausted for {url}")

    def get_all_pages(self, space_key: str) -> Iterator[dict]:
        limit = 100
        start = 0
        cql = f"space={space_key} and type=page"
        while True:
            data = self._get(
                "/wiki/rest/api/search",
                params={"cql": cql, "limit": limit, "start": start,
                        "expand": "version"},
            )
            results = data.get("results", [])
            for item in results:
                content = item.get("content", {})
                yield {
                    "page_id": content.get("id"),
                    "title": content.get("title"),
                    "url": (
                        self.base_url
                        + content.get("_links", {}).get("webui", "")
                    ),
                    "confluence_version": (
                        content.get("version", {}).get("number")
                    ),
                }
            if len(results) < limit:
                break
            start += limit

    def get_page_content(self, page_id: str) -> dict:
        data = self._get(
            f"/wiki/rest/api/content/{page_id}",
            params={"expand": "body.storage,version,metadata.labels"},
        )
        xhtml = data.get("body", {}).get("storage", {}).get("value", "")
        version = data.get("version", {}).get("number")
        labels = [
            lbl["name"]
            for lbl in data.get("metadata", {})
            .get("labels", {})
            .get("results", [])
        ]
        return {"xhtml": xhtml, "version": version, "labels": labels}
