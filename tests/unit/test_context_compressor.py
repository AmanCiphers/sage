import pytest

from app.context.compressor import (
    MAX_STRIKES,
    SUMMARY_PREFIX,
    ContextCompressor,
)
from app.inference.fake import FakeInferenceProvider
from app.inference.manager import InferenceManager
from app.inference.models import InferenceResponse, ModelCapabilities
from app.inference.registry import ModelRegistry
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import SessionStateStore


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


def build_compressor(database, scripted=None, should_fail=False):
    provider = FakeInferenceProvider(
        scripted=scripted or [],
        should_fail=should_fail,
        capabilities=ModelCapabilities(
            tool_calling=True, streaming=True, reasoning=True
        ),
    )
    registry = ModelRegistry()
    registry.register(provider)
    inference_manager = InferenceManager(registry)

    compressor = ContextCompressor(
        inference_manager=inference_manager,
        session_state=SessionStateStore(database),
        context_window=1000,
        threshold_ratio=0.5,
    )
    # Small tail budget so the fixture histories actually produce a middle.
    compressor.tail_budget = 100

    return compressor


def make_history(count: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} " + "x" * 200}
        for i in range(count)
    ]


def test_needs_compaction_threshold():
    compressor = ContextCompressor.__new__(ContextCompressor)
    compressor.threshold_tokens = 100
    compressor.calibration_ratio = 1.0

    small = [{"role": "user", "content": "hi"}]
    big = [{"role": "user", "content": "x" * 1000}]

    assert compressor.needs_compaction(small) is False
    assert compressor.needs_compaction(big) is True


def test_extract_tail_respects_budget():
    compressor = ContextCompressor.__new__(ContextCompressor)
    compressor.tail_budget = 30
    compressor.calibration_ratio = 1.0

    messages = make_history(20)
    tail = compressor.extract_tail(messages)

    assert tail == messages[-len(tail):]
    assert len(tail) < len(messages)
    assert tail[-1] == messages[-1]


def test_extract_tail_keeps_tool_groups_atomic():
    compressor = ContextCompressor.__new__(ContextCompressor)
    compressor.tail_budget = 30
    compressor.calibration_ratio = 1.0

    messages = [
        {"role": "user", "content": "do something"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        {"role": "user", "content": "thanks"},
    ]

    tail = compressor.extract_tail(messages)

    # The tool result and its assistant tool_call message stay together.
    assert messages[1] in tail
    assert messages[2] in tail


async def test_compress_produces_protected_head_summary_and_tail(database):
    compressor = build_compressor(
        database,
        scripted=[InferenceResponse(model="fake", content="Summary of work so far.")],
    )

    history = make_history(12)
    compacted = await compressor.compress(history)

    # v2 format: [protected_head, summary_row, protected_tail]
    # Protected head: first unique roles (user, assistant) = 2 messages
    assert len(compacted) >= 4  # head(2) + summary(1) + tail(>=1)
    assert compacted[2]["content"].startswith(SUMMARY_PREFIX)  # summary at index 2 (after 2 head msgs)
    assert "Summary of work so far." in compacted[2]["content"]
    # Tail should match end of history
    tail_start = -(len(compacted) - 3)  # everything after head(2) and summary(1)
    assert compacted[3:] == history[tail_start:]

    state = await compressor.session_state.get()
    assert state["summary"] == "Summary of work so far."
    assert state["ineffective_strikes"] == 0


async def test_compress_uses_fallback_when_llm_fails(database):
    compressor = build_compressor(database, should_fail=True)

    history = make_history(12)
    compacted = await compressor.compress(history)

    # v2 format: [head, summary, tail]
    assert compacted[2]["content"].startswith(SUMMARY_PREFIX)
    assert "Recent user requests:" in compacted[2]["content"]


async def test_anti_thrash_blocks_after_max_strikes(database):
    # Threshold deliberately tiny so compaction is never effective.
    compressor = build_compressor(
        database,
        scripted=[
            InferenceResponse(model="fake", content="summary")
            for _ in range(MAX_STRIKES + 1)
        ],
    )
    compressor.threshold_tokens = 10
    compressor.tail_budget = 40

    # Need enough history for: protected_head (2) + middle (several) + protected_tail (4)
    # So need at least 2 + 4 + 4 = 10 messages to have a middle region
    history = make_history(15)

    for _ in range(MAX_STRIKES):
        await compressor.compress(history)

    state = await compressor.session_state.get()
    assert state["ineffective_strikes"] == MAX_STRIKES

    # Blocked: history returned unchanged, no further LLM calls.
    result = await compressor.compress(history)
    assert result is history


async def test_excluded_summary_row_is_not_resummarized(database):
    compressor = build_compressor(
        database,
        scripted=[
            InferenceResponse(model="fake", content="first summary"),
            InferenceResponse(model="fake", content="second summary"),
        ],
    )

    history = make_history(12)
    first = await compressor.compress(history)

    # v2 format: first = [head, summary_row, tail]
    # Second compression: new activity arrives after first compaction
    # The old summary row must not be fed back in as conversation content.
    second = await compressor.compress([*first, *make_history(6)])

    state = await compressor.session_state.get()
    assert state["summary"] == "second summary"
    # Second result should have summary at index 2 (after head)
    assert second[2]["content"].startswith(SUMMARY_PREFIX)
    assert "first summary" not in second[2]["content"]
