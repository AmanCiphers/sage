import json

import pytest

from app.skills.store import SkillStore
from app.skills.tools import register_skill_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Tool registry whose skill store points at a temp directory."""
    monkeypatch.setattr(
        "app.skills.tools._resolve_skill_store",
        lambda: SkillStore(tmp_path / "skills.jsonl"),
    )
    reg = ToolRegistry()
    register_skill_tools(reg)
    return reg


def _call(registry, tool_name, **kwargs):
    return json.loads(registry.get(tool_name).handler(**kwargs))


def test_skill_view_with_version_does_not_crash(registry):
    """Regression: ``[v for v in v if ...]`` raised NameError on every
    versioned read."""
    created = _call(
        registry, "skill_manage", action="create", name="deploy", content="step 1"
    )
    skill_id = created["skill"]["skill_id"]

    result = _call(registry, "skill_view", skill_id=skill_id, version=1)

    assert result["skill_id"] == skill_id
    assert result["content"] == "step 1"


def test_skill_view_missing_version_returns_error(registry):
    created = _call(
        registry, "skill_manage", action="create", name="deploy", content="step 1"
    )
    result = _call(registry, "skill_view", skill_id=created["skill"]["skill_id"], version=99)
    assert "error" in result


def test_skill_view_latest_returns_content(registry):
    created = _call(
        registry, "skill_manage", action="create", name="deploy", content="step 1"
    )
    result = _call(registry, "skill_view", skill_id=created["skill"]["skill_id"])
    assert result["content"] == "step 1"
