from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os
from typing import Any

import requests

API_ROOT = "https://api.assignr.com/api/v2"
TOKEN_URL = "https://app.assignr.com/oauth/token"
ACCEPT = "application/vnd.assignr.v2.hal+json"


def parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return datetime.fromisoformat(value).date()


def normalize_api_url(url: str) -> str:
    if url.startswith("https://api.assignr.com/v2/"):
        return url.replace("https://api.assignr.com/v2/", "https://api.assignr.com/api/v2/", 1)
    return url


def extract_embedded_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    embedded = payload.get("_embedded", {})
    if isinstance(embedded, dict):
        for value in embedded.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def game_date(game: dict[str, Any]) -> date | None:
    for key in ("start_time", "game_date", "date", "localized_date"):
        raw = game.get(key)
        if not raw:
            continue
        if key == "localized_date":
            try:
                return datetime.strptime(str(raw), "%b %d %Y").date()
            except ValueError:
                continue
        try:
            return parse_date(str(raw))
        except ValueError:
            continue
    return None


def normalize_spaces(value: str) -> str:
    return " ".join(value.strip().split())


@dataclass
class AssignrClient:
    client_id: str
    client_secret: str
    site_id: str

    @classmethod
    def from_env(cls) -> "AssignrClient":
        client_id = os.getenv("ASSIGNR_CLIENT_ID", "").strip()
        client_secret = os.getenv("ASSIGNR_CLIENT_SECRET", "").strip()
        site_id = os.getenv("ASSIGNR_SITE_ID", "").strip()
        if not client_id or not client_secret or not site_id:
            raise ValueError("Set ASSIGNR_CLIENT_ID, ASSIGNR_CLIENT_SECRET, and ASSIGNR_SITE_ID.")
        return cls(client_id=client_id, client_secret=client_secret, site_id=site_id)

    def authenticate(self) -> str:
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "read",
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ValueError("Assignr token response did not include access_token.")
        return token

    def get_json(self, url: str, token: str) -> dict[str, Any]:
        response = requests.get(
            normalize_api_url(url),
            headers={
                "Accept": ACCEPT,
                "Content-Type": ACCEPT,
                "Authorization": f"Bearer {token}",
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Assignr API returned JSON that was not an object.")
        return payload

    def follow_paginated_collection(self, url: str, token: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        current_url = normalize_api_url(url)
        while current_url:
            payload = self.get_json(current_url, token)
            items.extend(extract_embedded_items(payload))
            links = payload.get("_links", {})
            next_link = links.get("next", {}) if isinstance(links, dict) else {}
            if isinstance(next_link, dict) and next_link.get("href"):
                current_url = normalize_api_url(str(next_link["href"]))
            else:
                current_url = ""
        return items

    def fetch_games(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        token = self.authenticate()
        games_url = f"{API_ROOT}/sites/{self.site_id}/games.json"
        games = self.follow_paginated_collection(games_url, token)
        kept: list[dict[str, Any]] = []
        for game in games:
            day = game_date(game)
            if day is None or day < start_date or day > end_date:
                continue
            kept.append(game)
        return kept

    @staticmethod
    def assignment_summary(game: dict[str, Any]) -> list[dict[str, str]]:
        summary: list[dict[str, str]] = []
        embedded = game.get("_embedded", {})
        assignments = embedded.get("assignments", []) if isinstance(embedded, dict) else []
        for assignment in assignments:
            if not isinstance(assignment, dict) or not assignment.get("assigned"):
                continue
            official = assignment.get("_embedded", {}).get("official", {})
            first = str(official.get("first_name", "")).strip()
            last = str(official.get("last_name", "")).strip()
            name = " ".join(part for part in (first, last) if part)
            if not name:
                continue
            summary.append(
                {
                    "name": name,
                    "position": normalize_spaces(str(assignment.get("position", ""))),
                }
            )
        return summary
