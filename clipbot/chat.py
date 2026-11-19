"""Läser chatten under sändningen (en av signalerna i sentimentanalysen).

Varje meddelande sparas med klockslag i en ChatLog. Analysen räknar sedan hur
aktiv chatten var och hur många skratt-/hype-emotes som skrevs, sekund för sekund.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import time
from dataclasses import dataclass, field

import requests

from .config import Config, Streamer
from .platforms import LiveInfo

log = logging.getLogger(__name__)


@dataclass
class ChatLog:
    messages: list[tuple[float, str]] = field(default_factory=list)  # (unix-tid, text)

    def add(self, text: str, t: float | None = None) -> None:
        self.messages.append((time.time() if t is None else t, text))

    def between(self, start: float, end: float) -> list[tuple[float, str]]:
        return [(t, m) for t, m in self.messages if start <= t < end]

    def prune(self, before: float) -> None:
        self.messages = [(t, m) for t, m in self.messages if t >= before]


def parse_irc_privmsg(line: str) -> str | None:
    """':nick!nick@nick.tmi.twitch.tv PRIVMSG #kanal :hej KEKW' -> 'hej KEKW'"""
    if " PRIVMSG " not in line:
        return None
    rest = line.split(" PRIVMSG ", 1)[1]
    if " :" not in rest:
        return None
    return rest.split(" :", 1)[1].strip()


async def twitch_chat(channel: str, chat: ChatLog, stop: asyncio.Event) -> None:
    """Anonym läsning av Twitch-chatten via IRC (kräver ingen inloggning)."""
    while not stop.is_set():
        try:
            reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6697, ssl=ssl.create_default_context())
            nick = f"justinfan{random.randint(10000, 99999)}"
            writer.write(f"NICK {nick}\r\nJOIN #{channel.lower()}\r\n".encode())
            await writer.drain()
            log.info("Twitch-chatt: ansluten till #%s", channel)
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=360)
                except asyncio.TimeoutError:
                    break
                if not raw:
                    break
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("PING"):
                    writer.write(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                    await writer.drain()
                    continue
                msg = parse_irc_privmsg(line)
                if msg:
                    chat.add(msg)
            writer.close()
        except Exception as exc:
            log.warning("Twitch-chatt #%s: %s – försöker igen", channel, exc)
        if not stop.is_set():
            await asyncio.sleep(5)


KICK_PUSHER_URL = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"


def kick_chatroom_id(slug: str) -> int | None:
    """Kicks officiella API ger inte chattrummets id; det här är Kicks webbsides-API (inofficiellt)."""
    try:
        resp = requests.get(
            f"https://kick.com/api/v2/channels/{slug}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        return int(resp.json()["chatroom"]["id"])
    except Exception as exc:
        log.warning("Kick: hittade inte chattrum för %s (%s). Sätt kick_chatroom_id i streamers.json.", slug, exc)
        return None


def parse_kick_event(raw: str) -> str | None:
    data = json.loads(raw)
    if data.get("event") != "App\\Events\\ChatMessageEvent":
        return None
    inner = data.get("data")
    inner = json.loads(inner) if isinstance(inner, str) else (inner or {})
    content = inner.get("content")
    return str(content).strip() if content else None


async def kick_chat(streamer: Streamer, chat: ChatLog, stop: asyncio.Event) -> None:
    import websockets

    room = streamer.kick_chatroom_id or await asyncio.to_thread(kick_chatroom_id, streamer.channel)
    if not room:
        return
    while not stop.is_set():
        try:
            async with websockets.connect(KICK_PUSHER_URL, ping_interval=60) as ws:
                await ws.send(json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
                log.info("Kick-chatt: ansluten till %s", streamer.channel)
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"event": "pusher:ping", "data": {}}))
                        continue
                    msg = parse_kick_event(raw)
                    if msg:
                        chat.add(msg)
        except Exception as exc:
            log.warning("Kick-chatt %s: %s – försöker igen", streamer.channel, exc)
        if not stop.is_set():
            await asyncio.sleep(5)


async def youtube_chat(video_id: str, api_key: str, chat: ChatLog, stop: asyncio.Event) -> None:
    """YouTube Data API v3. Varje hämtning kostar kvot, därför följer vi YouTubes egen väntetid."""
    base = "https://www.googleapis.com/youtube/v3"
    try:
        resp = await asyncio.to_thread(
            requests.get, f"{base}/videos",
            params={"part": "liveStreamingDetails", "id": video_id, "key": api_key}, timeout=20,
        )
        resp.raise_for_status()
        chat_id = resp.json()["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
    except Exception as exc:
        log.warning("YouTube-chatt för %s gick inte att öppna: %s", video_id, exc)
        return
    page = None
    while not stop.is_set():
        try:
            params = {"part": "snippet", "liveChatId": chat_id, "key": api_key, "maxResults": 2000}
            if page:
                params["pageToken"] = page
            resp = await asyncio.to_thread(requests.get, f"{base}/liveChat/messages", params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            now = time.time()
            for item in data.get("items", []):
                text = item.get("snippet", {}).get("displayMessage")
                if text:
                    chat.add(text, now)
            page = data.get("nextPageToken")
            wait = max(5.0, data.get("pollingIntervalMillis", 10000) / 1000)
        except Exception as exc:
            log.warning("YouTube-chatt: %s", exc)
            wait = 30.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass


def start_chat(info: LiveInfo, cfg: Config, chat: ChatLog, stop: asyncio.Event) -> asyncio.Task | None:
    s = info.streamer
    if s.platform == "twitch":
        return asyncio.create_task(twitch_chat(s.channel, chat, stop))
    if s.platform == "kick":
        return asyncio.create_task(kick_chat(s, chat, stop))
    if s.platform == "youtube" and cfg.youtube_api_key and info.video_id:
        return asyncio.create_task(youtube_chat(info.video_id, cfg.youtube_api_key, chat, stop))
    log.info("%s: ingen chatt-läsning, analysen använder bara ljudet", s.display)
    return None
