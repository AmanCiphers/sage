import pytest

from app.inference.fake import FakeInferenceProvider
from app.inference.fallback import InferenceFallback
from app.inference.models import InferenceRequest


@pytest.mark.asyncio
async def test_fallback_uses_next_provider_after_failure():
    primary = FakeInferenceProvider(
        model_id="primary",
        should_fail=True,
    )

    fallback = FakeInferenceProvider(
        model_id="fallback",
    )

    executor = InferenceFallback(
        providers=[primary, fallback]
    )

    response = await executor.generate(
        InferenceRequest(
            messages=[
                {"role": "user", "content": "Hello"},
            ]
        )
    )

    assert response.model == "fallback"


@pytest.mark.asyncio
async def test_fallback_returns_primary_when_successful():
    primary = FakeInferenceProvider(
        model_id="primary",
    )

    fallback = FakeInferenceProvider(
        model_id="fallback",
    )

    executor = InferenceFallback(
        providers=[primary, fallback]
    )

    response = await executor.generate(
        InferenceRequest(
            messages=[
                {"role": "user", "content": "Hello"},
            ]
        )
    )

    assert response.model == "primary"


@pytest.mark.asyncio
async def test_fallback_fails_when_all_providers_fail():
    primary = FakeInferenceProvider(
        model_id="primary",
        should_fail=True,
    )

    fallback = FakeInferenceProvider(
        model_id="fallback",
        should_fail=True,
    )

    executor = InferenceFallback(
        providers=[primary, fallback]
    )

    with pytest.raises(
        RuntimeError,
        match="All inference providers failed",
    ):
        await executor.generate(
            InferenceRequest(
                messages=[
                    {"role": "user", "content": "Hello"},
                ]
            )
        )
