"""Steg 3 i videon: Discord – notis när en streamer går live, och klippen skickas dit
för godkännande. Tryck "Godkänn" så publiceras klippet via Post for Me.

Knapparna hanteras i on_interaction, så de fungerar även efter att boten startats om.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord

from .clipper import make_upload_file
from .config import Config
from .postforme import PostForMe
from .store import Clip, ClipStore

log = logging.getLogger(__name__)


def clip_text(clip: Clip) -> str:
    chat = "\n".join(f"> {m[:120]}" for m in clip.chat_sample[-5:])
    return (
        f"🎬 **Nytt klipp: {clip.streamer}** ({clip.platform})\n"
        f"Varför: {clip.reason or 'ljudtopp'} · poäng {clip.score}\n"
        + (f"Chatten:\n{chat}\n" if chat else "")
        + f"\n**Bildtext:**\n{clip.caption}\n\n`id {clip.id}`"
    )


def buttons(clip_id: str, manual: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    label = "Godkänn – ge mig TikTok-filen" if manual else "Godkänn och publicera"
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.success, custom_id=f"approve:{clip_id}"))
    view.add_item(discord.ui.Button(label="Avvisa", style=discord.ButtonStyle.danger, custom_id=f"reject:{clip_id}"))
    return view


class ClipBot(discord.Client):
    def __init__(self, cfg: Config, store: ClipStore):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.store = store
        self.poster = PostForMe(cfg)
        self.ready = asyncio.Event()

    async def on_ready(self) -> None:
        log.info("Discord: inloggad som %s", self.user)
        self.ready.set()
        for clip in self.store.pending():
            if clip.discord_message_id is None:
                await self.send_clip(clip)

    async def _channel(self) -> discord.abc.Messageable | None:
        await self.ready.wait()
        ch = self.get_channel(self.cfg.discord_channel_id) or await self.fetch_channel(self.cfg.discord_channel_id)
        return ch

    async def notify(self, text: str) -> None:
        ch = await self._channel()
        if ch:
            await ch.send(text)

    async def send_clip(self, clip: Clip) -> None:
        ch = await self._channel()
        if not ch:
            return
        preview = Path(clip.preview)
        file = discord.File(preview) if preview.exists() else None
        msg = await ch.send(clip_text(clip), file=file, view=buttons(clip.id, self.cfg.manual_publishing))
        self.store.update(clip.id, discord_message_id=msg.id)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        custom_id = (interaction.data or {}).get("custom_id", "")
        if ":" not in custom_id:
            return
        action, clip_id = custom_id.split(":", 1)
        clip = self.store.clips.get(clip_id)
        if clip is None or action not in {"approve", "reject"}:
            await interaction.response.send_message("Klippet finns inte längre.", ephemeral=True)
            return
        if clip.status != "pending":
            await interaction.response.send_message(f"Redan hanterat ({clip.status}).", ephemeral=True)
            return

        if action == "reject":
            self.store.update(clip_id, status="rejected")
            await interaction.response.edit_message(content=clip_text(clip) + "\n\n❌ Avvisat", view=None)
            return

        self.store.update(clip_id, status="approved")
        waiting = "⏳ Gör filen klar för TikTok …" if self.cfg.manual_publishing else "⏳ Publicerar …"
        await interaction.response.edit_message(content=clip_text(clip) + f"\n\n{waiting}", view=None)
        result, upload = await asyncio.to_thread(self.publish, clip)
        await interaction.edit_original_response(content=clip_text(clip) + f"\n\n{result}")
        if upload:
            await interaction.followup.send(
                content=f"📲 **Ladda upp i TikTok:** spara videon, öppna TikTok → **+** → **Ladda upp**.\n"
                        f"Kopiera bildtexten:\n```\n{clip.caption}\n```",
                file=discord.File(upload),
            )

    def publish(self, clip: Clip) -> tuple[str, Path | None]:
        """Returnerar (statusrad, fil att skicka i Discord eller None)."""
        if self.cfg.manual_publishing:
            try:
                src = Path(clip.path)
                upload = make_upload_file(src, src.with_name(src.stem + "_tiktok.mp4"))
                self.store.update(clip.id, status="posted", post_result="manuell uppladdning")
                return "✅ Godkänt. Filen kommer här under – ladda upp den själv i TikTok (gratisläget).", upload
            except Exception as exc:
                log.error("Kunde inte göra TikTok-fil för %s: %s", clip.id, exc)
                self.store.update(clip.id, status="failed", post_result=str(exc)[:200])
                return f"⚠️ Kunde inte göra filen: {exc}", None
        if self.cfg.dry_run:
            self.store.update(clip.id, status="posted", post_result="dry-run")
            return "🧪 Torrkörning (DRY_RUN=true): skulle ha publicerats via Post for Me nu. Inget skickades.", None
        try:
            res = self.poster.post(Path(clip.path), clip.caption)
            self.store.update(clip.id, status="posted", post_result=str(res.get("id", res))[:200])
            return f"✅ Skickat till Post for Me (inlägg {res.get('id', '?')}).", None
        except Exception as exc:
            log.error("Publicering misslyckades för %s: %s", clip.id, exc)
            self.store.update(clip.id, status="failed", post_result=str(exc)[:200])
            return f"⚠️ Publiceringen misslyckades: {exc}", None
