"""Klipper ut ett ögonblick ur inspelningen och gör en stående TikTok-version (9:16).

Klippet börjar CLIP_PRE_SEC (standard 40 s, videon säger 35–45 s) före toppen,
så att uppbyggnaden kommer med, och slutar CLIP_POST_SEC efter.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .recorder import Chunk

DISCORD_MAX_BYTES = 9_500_000  # Discords gräns för uppladdning utan Nitro är 10 MB



def vertical_filter(width: int = 1080, height: int = 1920) -> str:
    """9:16 med suddig bakgrund. Bakgrunden suddas i liten storlek och skalas upp,
    vilket är mångdubbelt snabbare än att sudda i full storlek (viktigt på svaga datorer)."""
    bw, bh = width // 4, height // 4
    return (
        "[0:v]split=2[a][b];"
        f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=8:1,scale={width}:{height}[bg];"
        f"[b]scale={width}:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p[v]"
    )


VERTICAL_FILTER = vertical_filter()


def clip_window(peak_abs: float, pre: float, post: float, rec_end: float | None = None) -> tuple[float, float]:
    start = max(0.0, peak_abs - pre)
    end = peak_abs + post
    if rec_end is not None:
        end = min(end, rec_end)
    return start, end


def chunks_for(chunks: list[Chunk], start: float, end: float) -> list[Chunk]:
    return [c for c in chunks if c.end > start and c.start < end]


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg misslyckades: {proc.stderr[-800:]}")


def video_kbps_for(duration: float, max_bytes: int, audio_kbps: int, budget: float = 0.92) -> int:
    kbps = int(max_bytes * 8 / 1000 / max(duration, 1.0) * budget) - audio_kbps
    return max(300, min(kbps, 4500))


def cut_clip(chunks: list[Chunk], start: float, end: float, out: Path, vertical: bool = True,
             size: tuple[int, int] = (1080, 1920), preset: str = "superfast", max_bytes: int | None = None) -> Path:
    """Klipper ut start–end. Med max_bytes styrs bitraten så att filen ryms (t.ex. i Discord)."""
    parts = chunks_for(chunks, start, end)
    if not parts:
        raise ValueError(f"Ingen inspelning täcker {start:.0f}–{end:.0f} s")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        for c in parts:
            fh.write(f"file '{c.path.resolve().as_posix()}'\n")
        concat = fh.name
    offset = max(0.0, start - parts[0].start)
    duration = end - start
    audio_kbps = 96 if max_bytes else 128
    budget = 0.92
    try:
        for _ in range(3):
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concat,
                   "-ss", f"{offset:.2f}", "-t", f"{duration:.2f}"]
            if vertical:
                cmd += ["-filter_complex", vertical_filter(*size), "-map", "[v]", "-map", "0:a:0?"]
            else:
                cmd += ["-vf", f"scale=-2:{size[1] if size[1] < size[0] else size[0]},fps=30,format=yuv420p"]
            cmd += ["-c:v", "libx264", "-preset", preset]
            if max_bytes:
                kbps = video_kbps_for(duration, max_bytes, audio_kbps, budget)
                cmd += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
            else:
                cmd += ["-crf", "21"]
            cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", str(out)]
            _run(cmd)
            if not max_bytes or out.stat().st_size <= max_bytes:
                break
            budget *= 0.85
    finally:
        Path(concat).unlink(missing_ok=True)
    return out


def make_preview(clip: Path, out: Path) -> Path:
    """Liten version som får plats i Discord. Det fulla klippet är det som publiceras."""
    for height, crf in ((854, 30), (640, 34), (480, 38)):
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip), "-vf", f"scale=-2:{height}",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-maxrate", "1200k", "-bufsize", "2400k",
              "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)])
        if out.stat().st_size <= DISCORD_MAX_BYTES:
            break
    return out


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out or 0) or 60.0


def make_upload_file(clip: Path, out: Path, max_bytes: int = DISCORD_MAX_BYTES) -> Path:
    """TikTok-färdig fil (720x1280) som ryms i Discord, för att ladda upp själv i TikTok-appen.

    Bitraten räknas fram ur klippets längd så att filen hamnar under Discords gräns.
    """
    duration = _duration(clip)
    audio_kbps = 96
    budget = 0.92
    for _ in range(3):
        video_kbps = int(max_bytes * 8 / 1000 / duration * budget) - audio_kbps
        video_kbps = max(300, min(video_kbps, 4500))
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip), "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
              "-c:v", "libx264", "-preset", "medium", "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
              "-bufsize", f"{video_kbps * 2}k", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
              "-movflags", "+faststart", str(out)])
        if out.stat().st_size <= max_bytes:
            break
        budget *= 0.85
    return out
