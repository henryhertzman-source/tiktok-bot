"""Steg 2 i videon: Streamlink spelar in sändningen i bitar om 10 minuter.

streamlink --stdout <url> <kvalitet>  |  ffmpeg -c copy -f segment -segment_time 600 ...

ffmpeg skriver en CSV-lista (filnamn, start, slut i sekunder från inspelningens början) varje
gång en bit är klar. Det är den listan som analysen bevakar.
"""
from __future__ import annotations

import csv
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Chunk:
    path: Path
    start: float   # sekunder från inspelningens start
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def read_segment_list(list_file: Path, folder: Path) -> list[Chunk]:
    if not list_file.exists():
        return []
    chunks = []
    with list_file.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) >= 3:
                chunks.append(Chunk(folder / row[0], float(row[1]), float(row[2])))
    return chunks


class Recorder:
    def __init__(self, url: str, folder: Path, quality: str, chunk_seconds: int):
        self.url = url
        self.folder = folder
        self.quality = quality
        self.chunk_seconds = chunk_seconds
        self.list_file = folder / "chunks.csv"
        self.started_at = 0.0   # unix-tid då inspelningen startade (för att para ihop chatten)
        self._streamlink: subprocess.Popen | None = None
        self._ffmpeg: subprocess.Popen | None = None

    @staticmethod
    def check_tools() -> list[str]:
        return [t for t in ("streamlink", "ffmpeg", "ffprobe") if shutil.which(t) is None]

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._streamlink = subprocess.Popen(
            ["streamlink", "--stdout", "--twitch-disable-ads", "--retry-streams", "10", "--retry-max", "5",
             self.url, self.quality],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._ffmpeg = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?",
             "-c", "copy", "-f", "segment", "-segment_time", str(self.chunk_seconds), "-reset_timestamps", "1",
             "-segment_list", str(self.list_file), "-segment_list_type", "csv",
             str(self.folder / "chunk_%04d.ts")],
            stdin=self._streamlink.stdout, stderr=subprocess.DEVNULL,
        )
        self._streamlink.stdout.close()  # ffmpeg äger nu röret
        self.started_at = time.time()
        log.info("Spelar in %s -> %s", self.url, self.folder)

    @property
    def running(self) -> bool:
        return self._ffmpeg is not None and self._ffmpeg.poll() is None

    def stop(self) -> None:
        if self._streamlink and self._streamlink.poll() is None:
            self._streamlink.terminate()
        if self._ffmpeg:
            try:
                self._ffmpeg.wait(timeout=30)  # ffmpeg avslutar sista biten när strömmen tar slut
            except subprocess.TimeoutExpired:
                self._ffmpeg.terminate()
                self._ffmpeg.wait(timeout=10)
        log.info("Inspelningen stoppad: %s", self.folder)

    def chunks(self) -> list[Chunk]:
        return read_segment_list(self.list_file, self.folder)
