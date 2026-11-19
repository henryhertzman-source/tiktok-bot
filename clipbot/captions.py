"""Bildtext till klippet. Med ANTHROPIC_API_KEY skriver Claude den, annars används en mall."""
from __future__ import annotations

import logging

from .config import Config

log = logging.getLogger(__name__)

PROMPT = """Write a TikTok caption for a short gameplay clip from a live stream.

Streamer: {streamer}
Platform: {platform}
Stream title: {title}
Why the clip was picked: {reason}
Chat messages around the moment:
{chat}

Rules: one short hook line (max 80 characters) that makes people want to watch, then 4-6 relevant
hashtags (include the streamer's name and #gta). Credit the streamer. No false claims about what
happens in the clip; if the chat doesn't make it clear, keep the hook general. Reply with the caption only."""


def template_caption(streamer: str, title: str) -> str:
    tag = "".join(ch for ch in streamer if ch.isalnum())
    return f"{streamer} 😂 ({title[:60]})\n#{tag} #gta #gtarp #gaming #clips #fyp".replace(" ()", "")


def write_caption(cfg: Config, streamer: str, platform: str, title: str, reason: str, chat: list[str]) -> str:
    if not cfg.anthropic_api_key:
        return template_caption(streamer, title)
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        response = client.beta.messages.create(
            model=cfg.claude_model,
            max_tokens=1024,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": PROMPT.format(
                    streamer=streamer, platform=platform, title=title or "-", reason=reason or "-",
                    chat="\n".join(chat[-40:]) or "-",
                ),
            }],
        )
        if response.stop_reason == "refusal":
            return template_caption(streamer, title)
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or template_caption(streamer, title)
    except Exception as exc:
        log.warning("Claude kunde inte skriva bildtext (%s), använder mall", exc)
        return template_caption(streamer, title)
