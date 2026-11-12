import pytest

from app.config import settings
from app.context.builder import ContextBuilder


class _MessageStore:
    async def recent(self, limit):
        return []


class _SessionState:
    async def get(self):
        return {"summary": None}


class _Compressor:
    def extract_tail(self, messages):
        return []


@pytest.fixture
def builder():
    return ContextBuilder(
        message_store=_MessageStore(),
        session_state=_SessionState(),
        compressor=_Compressor(),
        system_prompt="SYSTEM PROMPT",
    )


def test_system_content_includes_live_clock(builder, monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Asia/Kolkata")

    content = builder._system_content()

    assert "SYSTEM PROMPT" in content
    assert "## Current date and time" in content
    assert "- Date:" in content
    assert "- Time:" in content
    assert "- Timezone: Asia/Kolkata" in content


@pytest.mark.asyncio
async def test_build_includes_live_clock(builder, monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Asia/Kolkata")

    history = await builder.build()

    assert history[0]["role"] == "system"
    assert "## Current date and time" in history[0]["content"]
