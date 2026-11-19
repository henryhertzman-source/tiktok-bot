"""Sentimentanalys av en inspelad bit (10 min) – hittar ögonblicken som är värda att klippa.

Signaler, som i videon:
  1. Skratt/ljud: plötsliga toppar i ljudnivån (skratt, skrik, reaktioner). Mäts med ffmpegs
     ebur128-filter (upplevd ljudstyrka, LUFS) sekund för sekund.
  2. Chattens aktivitet: hur många meddelanden per sekund jämfört med det normala.
  3. Vissa emotes/emojis: KEKW, LUL, 😂, 💀 osv. (HYPE_EMOTES).

Varje signal görs om till ett "hur ovanligt är det här"-värde (robust z-poäng mot bitens median)
och vägs ihop. Sekunder över SCORE_THRESHOLD blir klippkandidater.
"""
from __future__ import annotations

import re
import statistics
import subprocess
from dataclasses import dataclass

WINDOW = 5  # sekunder som räknas ihop för chatt-signalerna

_EBUR = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf)")


@dataclass
class Moment:
    t: float          # sekund inom biten där toppen ligger
    score: float
    reason: str


def audio_loudness(path: str) -> list[float]:
    """Ljudstyrka (LUFS, momentan) per sekund. Tom lista om filen saknar ljud."""
    proc = subprocess.run(
        ["ffmpeg", "-nostats", "-hide_banner", "-i", path, "-map", "0:a:0?", "-af", "ebur128=framelog=info", "-f", "null", "-"],
        capture_output=True, text=True, errors="ignore",
    )
    return parse_ebur128(proc.stderr)


def parse_ebur128(stderr: str) -> list[float]:
    per_sec: dict[int, float] = {}
    for m in _EBUR.finditer(stderr):
        sec = int(float(m.group(1)))
        val = -70.0 if m.group(2) == "-inf" else max(float(m.group(2)), -70.0)
        per_sec[sec] = max(per_sec.get(sec, -70.0), val)
    if not per_sec:
        return []
    return [per_sec.get(i, -70.0) for i in range(max(per_sec) + 1)]


def robust_z(values: list[float], floor: float) -> list[float]:
    """(x - median) / spridning. 'floor' hindrar att en nästan tyst chatt ger jättevärden."""
    if not values:
        return []
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values) * 1.4826
    scale = max(mad, floor)
    return [(v - med) / scale for v in values]


def _rolling_sum(values: list[float], window: int) -> list[float]:
    out, acc = [], 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc)
    return out


def is_hype(message: str, emotes: list[str]) -> bool:
    tokens = {t.strip(".,!?:;").lower() for t in message.split()}
    for e in emotes:
        if e.isascii():
            if e.lower() in tokens:
                return True
        elif e in message:
            return True
    return False


def score_chunk(
    duration: int,
    chat_rel: list[tuple[float, str]],
    loudness: list[float],
    emotes: list[str],
    w_chat: float = 1.0,
    w_emotes: float = 1.5,
    w_audio: float = 1.0,
) -> list[tuple[float, str]]:
    """Poäng per sekund. chat_rel = [(sekunder från bitens start, text)]."""
    n = max(duration, len(loudness), 1)
    chat_count = [0.0] * n
    hype_count = [0.0] * n
    for t, msg in chat_rel:
        i = int(t)
        if 0 <= i < n:
            chat_count[i] += 1
            if is_hype(msg, emotes):
                hype_count[i] += 1

    signals: list[tuple[str, float, list[float]]] = []
    if chat_rel:
        signals.append(("chatt", w_chat, robust_z(_rolling_sum(chat_count, WINDOW), floor=2.0)))
        signals.append(("emotes", w_emotes, robust_z(_rolling_sum(hype_count, WINDOW), floor=1.0)))
    if loudness:
        loud = (loudness + [-70.0] * n)[:n]
        signals.append(("ljud", w_audio, robust_z(loud, floor=3.0)))
    if not signals:
        return [(0.0, "")] * n

    total_w = sum(w for _, w, _ in signals) or 1.0
    out = []
    for i in range(n):
        parts, acc = [], 0.0
        for name, w, z in signals:
            pos = max(z[i], 0.0)
            acc += w * pos
            if pos >= 1.5:
                parts.append(f"{name} +{pos:.1f}")
        out.append((acc / total_w, ", ".join(parts)))
    return out


def pick_moments(scores: list[tuple[float, str]], threshold: float, max_moments: int, min_gap: float) -> list[Moment]:
    order = sorted(range(len(scores)), key=lambda i: scores[i][0], reverse=True)
    chosen: list[Moment] = []
    for i in order:
        score, reason = scores[i]
        if score < threshold or len(chosen) >= max_moments:
            break
        if all(abs(i - m.t) >= min_gap for m in chosen):
            chosen.append(Moment(t=float(i), score=score, reason=reason))
    return sorted(chosen, key=lambda m: m.t)
