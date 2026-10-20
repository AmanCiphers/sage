import pytest

from app.inference.fake import FakeInferenceProvider
from app.inference.registry import ModelRegistry


def test_register_and_get_model():
    registry = ModelRegistry()
    provider = FakeInferenceProvider()

    registry.register(provider)

    result = registry.get("fake-model")

    assert result is provider


def test_duplicate_model_registration_rejected():
    registry = ModelRegistry()

    registry.register(FakeInferenceProvider())

    with pytest.raises(ValueError, match="Model already registered"):
        registry.register(FakeInferenceProvider())


def test_unknown_model_rejected():
    registry = ModelRegistry()

    with pytest.raises(KeyError, match="Unknown model"):
        registry.get("does-not-exist")


def test_list_models():
    registry = ModelRegistry()
    registry.register(FakeInferenceProvider())

    models = registry.list()

    assert len(models) == 1
    assert models[0].model_id == "fake-model"
