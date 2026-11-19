"""Steg 1 i videon: fråga varje plattforms API vilka streamers som är live.

- Twitch: Helix API (gratis app på dev.twitch.tv, client credentials).
- Kick:   Kicks officiella publika API (gratis app på kick.com, client credentials).
- YouTube: kanalens /live-sida (kostar ingen API-kvot). Med YOUTUBE_API_KEY hämtas även chatten.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests

from .config import Config, Streamer

log = logging.getLogger(__name__)
TIMEOUT = 20
UA = "Mozilla/5.0 (compatible; clipbot/1.0)"


@dataclass
class LiveInfo:
    streamer: Streamer
    title: str = ""
    url: str = ""            # adressen Streamlink spelar in från
    video_id: str = ""       # YouTube: videons id (behövs för chatten)


class _ClientCredentials:
    """Hämtar och förnyar en app-token (OAuth client credentials)."""

    def __init__(self, token_url: str, client_id: str | None, client_secret: str | None):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._expires = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = requests.post(
            self.token_url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._expires = time.time() + float(data.get("expires_in", 3600))
        return self._token


class Twitch:
    def __init__(self, cfg: Config):
        self.auth = _ClientCredentials("https://id.twitch.tv/oauth2/token", cfg.twitch_client_id, cfg.twitch_client_secret)

    def live(self, streamers: list[Streamer]) -> dict[str, LiveInfo]:
        if not streamers:
            return {}
        if not self.auth.configured:
            log.warning("Twitch-streamers finns men TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET saknas")
            return {}
        by_login = {s.channel.lower(): s for s in streamers}
        result: dict[str, LiveInfo] = {}
        logins = list(by_login)
        for i in range(0, len(logins), 100):  # Helix tar max 100 per anrop
            resp = requests.get(
                "https://api.twitch.tv/helix/streams",
                params=[("user_login", l) for l in logins[i : i + 100]],
                headers={"Client-Id": self.auth.client_id, "Authorization": f"Bearer {self.auth.token()}"},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            result.update(parse_twitch_streams(resp.json(), by_login))
        return result


def parse_twitch_streams(payload: dict, by_login: dict[str, Streamer]) -> dict[str, LiveInfo]:
    out: dict[str, LiveInfo] = {}
    for item in payload.get("data", []):
        login = str(item.get("user_login", "")).lower()
        s = by_login.get(login)
        if s and item.get("type") == "live":
            out[s.key] = LiveInfo(s, title=item.get("title", ""), url=f"https://www.twitch.tv/{login}")
    return out


class Kick:
    def __init__(self, cfg: Config):
        self.auth = _ClientCredentials("https://id.kick.com/oauth/token", cfg.kick_client_id, cfg.kick_client_secret)

    def live(self, streamers: list[Streamer]) -> dict[str, LiveInfo]:
        if not streamers:
            return {}
        if not self.auth.configured:
            log.warning("Kick-streamers finns men KICK_CLIENT_ID/KICK_CLIENT_SECRET saknas")
            return {}
        by_slug = {s.channel.lower(): s for s in streamers}
        resp = requests.get(
            "https://api.kick.com/public/v1/channels",
            params=[("slug", s) for s in by_slug],
            headers={"Authorization": f"Bearer {self.auth.token()}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return parse_kick_channels(resp.json(), by_slug)


def parse_kick_channels(payload: dict, by_slug: dict[str, Streamer]) -> dict[str, LiveInfo]:
    out: dict[str, LiveInfo] = {}
    for item in payload.get("data", []):
        slug = str(item.get("slug", "")).lower()
        s = by_slug.get(slug)
        stream = item.get("stream") or {}
        if s and stream.get("is_live"):
            out[s.key] = LiveInfo(s, title=item.get("stream_title", ""), url=f"https://kick.com/{slug}")
    return out


class YouTube:
    def live(self, streamers: list[Streamer]) -> dict[str, LiveInfo]:
        out: dict[str, LiveInfo] = {}
        for s in streamers:
            ch = s.channel
            base = f"https://www.youtube.com/{ch}" if ch.startswith("@") else f"https://www.youtube.com/channel/{ch}"
            try:
                resp = requests.get(
                    f"{base}/live",
                    headers={"User-Agent": UA, "Accept-Language": "en"},
                    cookies={"CONSENT": "YES+1"},
                    timeout=TIMEOUT,
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                log.warning("YouTube %s: %s", ch, exc)
                continue
            info = parse_youtube_live_page(resp.text, s)
            if info:
                out[s.key] = info
        return out


_VIDEO_ID = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"')
_TITLE = re.compile(r'<meta name="title" content="([^"]*)"')


def parse_youtube_live_page(html: str, s: Streamer) -> LiveInfo | None:
    """/live-sidan pekar på pågående sändning. isLiveNow skiljer den från en planerad premiär."""
    m = _VIDEO_ID.search(html)
    if not m or '"isLiveNow":true' not in html:
        return None
    title = _TITLE.search(html)
    vid = m.group(1)
    return LiveInfo(s, title=title.group(1) if title else "", url=f"https://www.youtube.com/watch?v={vid}", video_id=vid)


class LiveChecker:
    """Kör alla plattformar och returnerar {streamer.key: LiveInfo} för de som är live."""

    def __init__(self, cfg: Config):
        self.twitch = Twitch(cfg)
        self.kick = Kick(cfg)
        self.youtube = YouTube()

    def check(self, streamers: list[Streamer]) -> dict[str, LiveInfo]:
        result: dict[str, LiveInfo] = {}
        for platform, client in (("twitch", self.twitch), ("kick", self.kick), ("youtube", self.youtube)):
            group = [s for s in streamers if s.platform == platform]
            if not group:
                continue
            try:
                result.update(client.live(group))
            except Exception as exc:  # en plattform som strular får inte stoppa de andra
                log.error("Kunde inte kolla %s: %s", platform, exc)
        return result
