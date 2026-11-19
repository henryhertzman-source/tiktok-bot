"""Alla inställningar läses från miljövariabler (eller en lokal .env-fil).

Inga hemligheter står i koden. Se .env.example för en komplett lista.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return (_env(name, "true" if default else "false") or "").lower() in {"1", "true", "yes", "ja", "on"}


def _list(name: str, default: str) -> list[str]:
    return [p.strip() for p in (_env(name, default) or "").split(",") if p.strip()]


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env-läsare (inget extra paket behövs)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.split(" #", 1)[0]
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


DEFAULT_HYPE = (
    "KEKW,LUL,LULW,OMEGALUL,LMAO,LOL,HAHA,ICANT,Pog,PogChamp,POGGERS,W,"
    "monkaS,EZ,Clap,😂,🤣,💀,😭,🔥,😱"
)


@dataclass
class Streamer:
    platform: str               # "twitch", "kick" eller "youtube"
    channel: str                # Twitch-login, Kick-slug, YouTube-kanal-id (UC...) eller @handle
    name: str = ""              # visningsnamn i Discord och bildtexter
    kick_chatroom_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.channel.lower()}"

    @property
    def display(self) -> str:
        return self.name or self.channel


@dataclass
class Config:
    dry_run: bool = True

    # Discord (notiser + godkännande)
    discord_token: str | None = None
    discord_channel_id: int | None = None

    # Plattformar
    twitch_client_id: str | None = None
    twitch_client_secret: str | None = None
    kick_client_id: str | None = None
    kick_client_secret: str | None = None
    youtube_api_key: str | None = None     # valfri: bara för YouTube-chatt

    # Post for Me
    postforme_api_key: str | None = None
    postforme_account_ids: list[str] = field(default_factory=list)
    postforme_base_url: str = "https://api.postforme.dev"

    # Claude (valfritt: bildtexter)
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"

    # Inspelning
    data_dir: Path = Path("data")
    streamers_file: Path = Path("streamers.json")
    poll_interval_sec: int = 120
    chunk_seconds: int = 600
    stream_quality: str = "720p60,720p,best"
    keep_recordings: bool = False

    # Klipp och analys
    clip_pre_sec: float = 40.0
    clip_post_sec: float = 15.0
    score_threshold: float = 2.5
    max_clips_per_chunk: int = 3
    min_gap_sec: float = 60.0
    weight_chat: float = 1.0
    weight_emotes: float = 1.5
    weight_audio: float = 1.0
    hype_emotes: list[str] = field(default_factory=list)
    vertical: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        channel = _env("DISCORD_CHANNEL_ID")
        return cls(
            dry_run=_bool("DRY_RUN", True),
            discord_token=_env("DISCORD_BOT_TOKEN"),
            discord_channel_id=int(channel) if channel else None,
            twitch_client_id=_env("TWITCH_CLIENT_ID"),
            twitch_client_secret=_env("TWITCH_CLIENT_SECRET"),
            kick_client_id=_env("KICK_CLIENT_ID"),
            kick_client_secret=_env("KICK_CLIENT_SECRET"),
            youtube_api_key=_env("YOUTUBE_API_KEY"),
            postforme_api_key=_env("POSTFORME_API_KEY"),
            postforme_account_ids=_list("POSTFORME_ACCOUNT_IDS", ""),
            postforme_base_url=_env("POSTFORME_BASE_URL", "https://api.postforme.dev"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            claude_model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
            data_dir=Path(_env("DATA_DIR", "data")),
            streamers_file=Path(_env("STREAMERS_FILE", "streamers.json")),
            poll_interval_sec=_int("POLL_INTERVAL_SEC", 120),
            chunk_seconds=_int("CHUNK_SECONDS", 600),
            stream_quality=_env("STREAM_QUALITY", "720p60,720p,best"),
            keep_recordings=_bool("KEEP_RECORDINGS", False),
            clip_pre_sec=_float("CLIP_PRE_SEC", 40.0),
            clip_post_sec=_float("CLIP_POST_SEC", 15.0),
            score_threshold=_float("SCORE_THRESHOLD", 2.5),
            max_clips_per_chunk=_int("MAX_CLIPS_PER_CHUNK", 3),
            min_gap_sec=_float("MIN_GAP_SEC", 60.0),
            weight_chat=_float("WEIGHT_CHAT", 1.0),
            weight_emotes=_float("WEIGHT_EMOTES", 1.5),
            weight_audio=_float("WEIGHT_AUDIO", 1.0),
            hype_emotes=_list("HYPE_EMOTES", DEFAULT_HYPE),
            vertical=_bool("VERTICAL", True),
        )


def load_streamers(path: Path) -> list[Streamer]:
    """Läser streamers.json: [{"platform": "twitch", "channel": "xqc", "name": "xQc"}, ...]"""
    if not path.exists():
        raise FileNotFoundError(f"{path} saknas. Kopiera streamers.example.json till {path} och fyll i.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    streamers: list[Streamer] = []
    for item in raw:
        platform = str(item["platform"]).lower().strip()
        if platform not in {"twitch", "kick", "youtube"}:
            raise ValueError(f"Okänd plattform '{platform}' i {path} (tillåtna: twitch, kick, youtube)")
        streamers.append(
            Streamer(
                platform=platform,
                channel=str(item["channel"]).strip(),
                name=str(item.get("name", "")).strip(),
                kick_chatroom_id=item.get("kick_chatroom_id"),
            )
        )
    return streamers
