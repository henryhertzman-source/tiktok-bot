"""Håller reda på klippen (väntar / godkänd / avvisad / publicerad) i data/clips.json."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Clip:
    streamer: str               # visningsnamn
    platform: str
    stream_title: str
    path: str                   # fullstor 9:16-fil som publiceras
    preview: str                # liten fil för Discord
    score: float
    reason: str
    chat_sample: list[str] = field(default_factory=list)
    caption: str = ""
    status: str = "pending"     # pending | approved | rejected | posted | failed
    post_result: str = ""
    discord_message_id: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created: float = field(default_factory=time.time)


class ClipStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.clips: dict[str, Clip] = {}
        if path.exists():
            for item in json.loads(path.read_text(encoding="utf-8")):
                clip = Clip(**item)
                self.clips[clip.id] = clip

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(c) for c in self.clips.values()], ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def add(self, clip: Clip) -> Clip:
        self.clips[clip.id] = clip
        self.save()
        return clip

    def update(self, clip_id: str, **changes) -> Clip:
        clip = self.clips[clip_id]
        for k, v in changes.items():
            setattr(clip, k, v)
        self.save()
        return clip

    def pending(self) -> list[Clip]:
        return [c for c in self.clips.values() if c.status == "pending"]
