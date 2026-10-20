from config.model_catalog import get_model_catalog


def test_model_catalog_contains_primary_and_fallback():
    models = get_model_catalog()

    assert len(models) >= 2
    providers = {model.provider for model in models}
    assert providers == {"nebius", "nvidia"}

    for model in models:
        assert model.model_id
        assert model.context_window > 0


def test_nebius_flash_is_primary_chat_model():
    """Chat turns (tool-calling, no reasoning) pick the highest-priority
    compatible model — the Nebius flash model, with gpt-oss as fallback."""

    models = get_model_catalog()
    by_id = {model.model_id: model for model in models}

    nebius = by_id["deepseek-ai/DeepSeek-V4-Flash-0731"]
    nvidia = by_id["openai/gpt-oss-20b"]

    assert nebius.provider == "nebius"
    assert nebius.priority > nvidia.priority
    assert nebius.capabilities.tool_calling is True

    # Compaction requires reasoning → stays on gpt-oss.
    assert nvidia.capabilities.reasoning is True
    assert nebius.capabilities.reasoning is False
