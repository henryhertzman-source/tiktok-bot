"""Startpunkt.

  python -m clipbot run              kör allt: livekoll var 2:a minut, inspelning, klipp, Discord
  python -m clipbot check            kolla en gång vilka streamers som är live (testar API-nycklarna)
  python -m clipbot accounts         lista kontona i Post for Me (för POSTFORME_ACCOUNT_IDS)
  python -m clipbot analyze <fil>    testa klippningen på en egen videofil (ingen chatt)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from .config import Config, load_dotenv, load_streamers
from .platforms import LiveChecker, LiveInfo
from .postforme import PostForMe
from .recorder import Recorder
from .session import Session, analyze_file
from .store import Clip, ClipStore

log = logging.getLogger("clipbot")


async def run(cfg: Config) -> None:
    missing = Recorder.check_tools()
    if missing:
        raise SystemExit(f"Saknas: {', '.join(missing)}. Installera dem först (se README).")
    streamers = load_streamers(cfg.streamers_file)
    store = ClipStore(cfg.data_dir / "clips.json")
    checker = LiveChecker(cfg)

    bot = None
    if cfg.discord_token and cfg.discord_channel_id:
        from .discord_bot import ClipBot

        bot = ClipBot(cfg, store)
        asyncio.create_task(bot.start(cfg.discord_token))
    else:
        log.warning("DISCORD_BOT_TOKEN/DISCORD_CHANNEL_ID saknas: klippen sparas i %s men kan inte godkännas", cfg.data_dir)

    async def notify(text: str) -> None:
        log.info(text)
        if bot:
            try:
                await bot.notify(text)
            except Exception as exc:
                log.error("Discord-notis misslyckades: %s", exc)

    async def on_clip(clip: Clip) -> None:
        if bot:
            try:
                await bot.send_clip(clip)
            except Exception as exc:
                log.error("Kunde inte skicka klipp till Discord: %s", exc)

    sessions: dict[str, tuple[Session, asyncio.Task]] = {}
    mode = "gratisläge: du laddar upp i TikTok själv" if cfg.manual_publishing else (
        "Post for Me" + (" (TORRKÖRNING)" if cfg.dry_run else ""))
    log.info("Bevakar %d streamers, kollar var %d s. Publicering: %s", len(streamers), cfg.poll_interval_sec, mode)
    while True:
        live: dict[str, LiveInfo] = await asyncio.to_thread(checker.check, streamers)
        for key, info in live.items():
            if key not in sessions or sessions[key][1].done():
                await notify(f"🔴 **{info.streamer.display}** är live på {info.streamer.platform}: {info.title}\n"
                             f"Börjar spela in. {info.url}")
                session = Session(info, cfg, store, on_clip)
                sessions[key] = (session, asyncio.create_task(session.run()))
        for key in [k for k in sessions if k not in live]:
            session, task = sessions.pop(key)
            session.stop.set()
            await notify(f"⚫ **{session.info.streamer.display}** har gått offline. Avslutar inspelningen.")
        await asyncio.sleep(cfg.poll_interval_sec)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()
    cfg = Config.from_env()
    cmd = argv[0] if argv else "run"

    if cmd == "run":
        asyncio.run(run(cfg))
    elif cmd == "check":
        streamers = load_streamers(cfg.streamers_file)
        live = LiveChecker(cfg).check(streamers)
        for s in streamers:
            info = live.get(s.key)
            print(f"{'LIVE   ' if info else 'offline'}  {s.platform:8} {s.display}" + (f"  – {info.title}" if info else ""))
    elif cmd == "accounts":
        for acc in PostForMe(cfg).accounts():
            print(f"{acc.get('id')}  {acc.get('platform')}  {acc.get('username') or acc.get('name', '')}")
    elif cmd == "analyze" and len(argv) > 1:
        analyze_file(cfg, Path(argv[1]))
    else:
        print(__doc__)
        raise SystemExit(2)
