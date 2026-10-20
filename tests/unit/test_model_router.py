import pytest

from app.inference.fake import FakeInferenceProvider
from app.inference.models import ModelCapabilities, ModelRequirements
from app.inference.registry import ModelRegistry
from app.inference.router import ModelRouter


def test_router_selects_compatible_model():
    registry = ModelRegistry()

    registry.register(
        FakeInferenceProvider(
            model_id="fast-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
                streaming=True,
            ),
        )
    )

    router = ModelRouter(registry)

    provider = router.route(
        ModelRequirements(
            tool_calling=True,
        )
    )

    assert provider.metadata.model_id == "fast-model"


def test_router_selects_reasoning_model_when_required():
    registry = ModelRegistry()

    registry.register(
        FakeInferenceProvider(
            model_id="fast-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
                streaming=True,
            ),
        )
    )

    registry.register(
        FakeInferenceProvider(
            model_id="reasoning-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
                reasoning=True,
                streaming=True,
            ),
        )
    )

    router = ModelRouter(registry)

    provider = router.route(
        ModelRequirements(
            tool_calling=True,
            reasoning=True,
        )
    )

    assert provider.metadata.model_id == "reasoning-model"


def test_router_rejects_incompatible_requirements():
    registry = ModelRegistry()

    registry.register(
        FakeInferenceProvider(
            model_id="fast-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
            ),
        )
    )

    router = ModelRouter(registry)

    with pytest.raises(
        RuntimeError,
        match="No model satisfies",
    ):
        router.route(
            ModelRequirements(
                vision=True,
            )
        )


def test_router_supports_multiple_requirements():
    registry = ModelRegistry()

    registry.register(
        FakeInferenceProvider(
            model_id="reasoning-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
                reasoning=True,
                streaming=True,
            ),
        )
    )

    router = ModelRouter(registry)

    provider = router.route(
        ModelRequirements(
            tool_calling=True,
            reasoning=True,
            streaming=True,
        )
    )

    assert provider.metadata.model_id == "reasoning-model"
    
    
def test_router_prefers_higher_priority_model():
    registry = ModelRegistry()

    registry.register(
        FakeInferenceProvider(
            model_id="fast-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
            ),
            priority=50,
        )
    )

    registry.register(
        FakeInferenceProvider(
            model_id="preferred-model",
            capabilities=ModelCapabilities(
                tool_calling=True,
            ),
            priority=100,
        )
    )

    router = ModelRouter(registry)

    provider = router.route(
        ModelRequirements(
            tool_calling=True,
        )
    )

    assert provider.metadata.model_id == "preferred-model"
