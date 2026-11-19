"""Steg 4 i videon: Post for Me – ett API som publicerar på alla kopplade sociala konton.

Flöde: be om en uppladdningsadress -> ladda upp videon dit -> skapa inlägget med
bildtext och vilka konton (TikTok, YouTube Shorts, Instagram …) det ska ut på.

OBS: endpoints och fältnamn är skrivna efter Post for Me:s dokumentation men inte provade
mot det riktiga API:t. Kör `python -m clipbot accounts` först – fungerar den är nyckeln rätt.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from .config import Config

log = logging.getLogger(__name__)
TIMEOUT = 60


class PostForMe:
    def __init__(self, cfg: Config):
        self.base = cfg.postforme_base_url.rstrip("/")
        self.key = cfg.postforme_api_key
        self.account_ids = cfg.postforme_account_ids

    @property
    def configured(self) -> bool:
        return bool(self.key and self.account_ids)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def accounts(self) -> list[dict]:
        resp = requests.get(f"{self.base}/v1/social-accounts", headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data

    def upload(self, video: Path) -> str:
        resp = requests.post(f"{self.base}/v1/media/create-upload-url", headers=self._headers(), json={}, timeout=TIMEOUT)
        resp.raise_for_status()
        info = resp.json()
        with video.open("rb") as fh:
            put = requests.put(info["upload_url"], data=fh, headers={"Content-Type": "video/mp4"}, timeout=600)
        put.raise_for_status()
        return info["media_url"]

    def post(self, video: Path, caption: str) -> dict:
        media_url = self.upload(video)
        body = {"caption": caption, "social_accounts": self.account_ids, "media": [{"url": media_url}]}
        resp = requests.post(f"{self.base}/v1/social-posts", headers=self._headers(), json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
