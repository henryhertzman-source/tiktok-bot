"""En inspelningssession per streamer som är live: spela in -> analysera varje bit -> klipp."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import analysis
from .captions import write_caption
from .chat import ChatLog, start_chat
from .clipper import DISCORD_MAX_BYTES, clip_window, cut_clip, make_preview
from .config import Config
from .platforms import LiveInfo
from .recorder import Chunk, Recorder
from .store import Clip, ClipStore

log = logging.getLogger(__name__)
CHECK_EVERY = 10  # sekunder mellan kontroller av nya bitar


@dataclass
class PendingCut:
    start: float
    end: float
    peak: float
    score: float
    reason: str


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)[:40] or "stream"


def find_moments(cfg: Config, chunk: Chunk, chat_rel: list[tuple[float, str]]) -> list[analysis.Moment]:
    loudness = analysis.audio_loudness(str(chunk.path))
    scores = analysis.score_chunk(
        int(chunk.duration) + 1, chat_rel, loudness, cfg.hype_emotes,
        cfg.weight_chat, cfg.weight_emotes, cfg.weight_audio,
    )
    return analysis.pick_moments(scores, cfg.score_threshold, cfg.max_clips_per_chunk, cfg.min_gap_sec)


def deletable(chunks: list[Chunk], keep_from: float) -> list[Chunk]:
    """Bitar som slutar före keep_from behövs inte längre (inga klipp väntar på dem)."""
    return [c for c in chunks if c.end < keep_from]


class Session:
    def __init__(self, info: LiveInfo, cfg: Config, store: ClipStore, on_clip: Callable[[Clip], Awaitable[None]]):
        self.info = info
        self.cfg = cfg
        self.store = store
        self.on_clip = on_clip
        self.chat = ChatLog()
        self.stop = asyncio.Event()

    async def run(self) -> None:
        chat_task = start_chat(self.info, self.cfg, self.chat, self.stop)
        try:
            while not self.stop.is_set():
                await self._record_once()
                if not self.stop.is_set():  # strömmen bröts men streamern är fortfarande live
                    log.info("%s: inspelningen avbröts, startar om om 30 s", self.info.streamer.display)
                    try:
                        await asyncio.wait_for(self.stop.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        pass
        finally:
            if chat_task:
                chat_task.cancel()

    async def _record_once(self) -> None:
        s = self.info.streamer
        folder = self.cfg.data_dir / "recordings" / f"{s.platform}_{safe_name(s.channel)}" / time.strftime("%Y%m%d-%H%M%S")
        rec = Recorder(self.info.url, folder, self.cfg.stream_quality, self.cfg.chunk_seconds)
        rec.start()
        analyzed: set[str] = set()
        pending: list[PendingCut] = []
        while rec.running and not self.stop.is_set():
            await self._process(rec, analyzed, pending, final=False)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=CHECK_EVERY)
            except asyncio.TimeoutError:
                pass
        await asyncio.to_thread(rec.stop)
        await self._process(rec, analyzed, pending, final=True)
        if not rec.chunks() and not analyzed:
            log.error("%s: inspelningen gav ingen video. Streamlink sa:\n%s",
                      self.info.streamer.display, rec.log_tail() or "(inget)")

    async def _process(self, rec: Recorder, analyzed: set[str], pending: list[PendingCut], final: bool) -> None:
        chunks = rec.chunks()  # ffmpeg listar bara färdiga bitar
        for chunk in chunks:
            if chunk.path.name in analyzed or not chunk.path.exists():
                continue
            analyzed.add(chunk.path.name)
            t0 = rec.started_at + chunk.start
            chat_rel = [(t - t0, m) for t, m in self.chat.between(t0, rec.started_at + chunk.end)]
            moments = await asyncio.to_thread(find_moments, self.cfg, chunk, chat_rel)
            log.info("%s: %s analyserad, %d chattmeddelanden, %d ögonblick",
                     self.info.streamer.display, chunk.path.name, len(chat_rel), len(moments))
            for m in moments:
                peak = chunk.start + m.t
                start, end = clip_window(peak, self.cfg.clip_pre_sec, self.cfg.clip_post_sec)
                pending.append(PendingCut(start, end, peak, m.score, m.reason))

        rec_end = chunks[-1].end if chunks else 0.0
        for cut in list(pending):
            if cut.end <= rec_end or final:
                pending.remove(cut)
                try:
                    await self._make_clip(rec, chunks, cut, rec_end)
                except Exception as exc:
                    log.error("Kunde inte göra klipp vid %.0f s: %s", cut.peak, exc)

        if not self.cfg.keep_recordings and chunks:
            keep_from = min([c.start for c in pending] + [chunks[-1].start - self.cfg.clip_pre_sec])
            for c in deletable(chunks, keep_from):
                c.path.unlink(missing_ok=True)

    async def _make_clip(self, rec: Recorder, chunks: list[Chunk], cut: PendingCut, rec_end: float) -> None:
        s = self.info.streamer
        end = min(cut.end, rec_end) if rec_end else cut.end
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(rec.started_at + cut.peak))
        out = self.cfg.data_dir / "clips" / f"{safe_name(s.display)}_{stamp}.mp4"
        if self.cfg.manual_publishing:
            # Gratisläget: EN fil i 720x1280 under Discords gräns = förhandsvisning och TikTok-fil på samma gång.
            await asyncio.to_thread(cut_clip, chunks, cut.start, end, out, self.cfg.vertical, (720, 1280),
                                    self.cfg.clip_preset, DISCORD_MAX_BYTES)
            preview = out
        else:
            await asyncio.to_thread(cut_clip, chunks, cut.start, end, out, self.cfg.vertical, (1080, 1920),
                                    self.cfg.clip_preset)
            preview = await asyncio.to_thread(make_preview, out, out.with_name(out.stem + "_preview.mp4"))
        wall = rec.started_at + cut.peak
        sample = [m for _, m in self.chat.between(wall - 20, wall + 10)][-40:]
        caption = await asyncio.to_thread(write_caption, self.cfg, s.display, s.platform, self.info.title, cut.reason, sample)
        clip = self.store.add(Clip(
            streamer=s.display, platform=s.platform, stream_title=self.info.title, path=str(out), preview=str(preview),
            score=round(cut.score, 2), reason=cut.reason, chat_sample=sample[-8:], caption=caption,
        ))
        log.info("Nytt klipp %s (%s, poäng %.1f)", clip.id, s.display, cut.score)
        await self.on_clip(clip)


def analyze_file(cfg: Config, video: Path) -> list[Path]:
    """Testläge: analysera en vanlig videofil (bara ljudet, ingen chatt) och klipp ut topparna."""
    import subprocess

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    ).stdout.strip() or 0)
    chunk = Chunk(video, 0.0, dur)
    outs = []
    for m in find_moments(cfg, chunk, []):
        start, end = clip_window(m.t, cfg.clip_pre_sec, cfg.clip_post_sec, rec_end=dur)
        out = cfg.data_dir / "clips" / f"test_{int(m.t)}s.mp4"
        cut_clip([chunk], start, end, out, cfg.vertical, preset=cfg.clip_preset)
        print(f"Topp vid {m.t:.0f} s (poäng {m.score:.1f}: {m.reason}) -> {out}")
        outs.append(out)
    if not outs:
        print("Inga ögonblick över tröskeln. Sänk SCORE_THRESHOLD för att få fler.")
    return outs
