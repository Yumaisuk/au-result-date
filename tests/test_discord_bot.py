import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

import discord_bot


class _FakeMessage:
    """Stands in for an InteractionMessage."""

    def __init__(self, fail=False):
        self.id = 111
        self.channel = SimpleNamespace(id=222)
        self.fail = fail
        self.edits = []

    async def edit(self, content):
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=401, reason="x"), "Invalid Webhook Token")
        self.edits.append(content)


class _FakeInteraction:
    def __init__(self, expired):
        self._expired = expired

    def is_expired(self):
        return self._expired


def _patch_partial_edits(monkeypatch):
    """Route client.get_partial_messageable(...).get_partial_message(...).edit(...) to a list."""
    sent = []

    class _Partial:
        def __init__(self, channel_id, message_id):
            self.ids = (channel_id, message_id)

        async def edit(self, content):
            sent.append((self.ids, content))

    class _Messageable:
        def __init__(self, channel_id):
            self.channel_id = channel_id

        def get_partial_message(self, message_id):
            return _Partial(self.channel_id, message_id)

    monkeypatch.setattr(discord_bot.client, "get_partial_messageable", lambda cid: _Messageable(cid))
    return sent


def test_safe_edit_uses_interaction_token_while_valid(monkeypatch):
    sent = _patch_partial_edits(monkeypatch)
    msg = _FakeMessage()
    asyncio.run(discord_bot._safe_edit(msg, "hi", _FakeInteraction(expired=False)))
    assert msg.edits == ["hi"]
    assert sent == []


def test_safe_edit_switches_to_bot_token_after_15_minutes(monkeypatch):
    sent = _patch_partial_edits(monkeypatch)
    msg = _FakeMessage()
    asyncio.run(discord_bot._safe_edit(msg, "late", _FakeInteraction(expired=True)))
    assert msg.edits == []
    assert sent == [((222, 111), "late")]


def test_safe_edit_falls_back_to_bot_token_when_interaction_edit_fails(monkeypatch):
    sent = _patch_partial_edits(monkeypatch)
    msg = _FakeMessage(fail=True)
    asyncio.run(discord_bot._safe_edit(msg, "x", _FakeInteraction(expired=False)))
    assert sent == [((222, 111), "x")]
