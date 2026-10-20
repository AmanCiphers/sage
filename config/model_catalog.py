from app.inference.models import ModelMetadata

from .models import NEBIUS_MODELS, NVIDIA_MODELS


MODEL_CATALOG: list[ModelMetadata] = [
    *NEBIUS_MODELS,
    *NVIDIA_MODELS,
]


def get_model_catalog() -> list[ModelMetadata]:
    return list(MODEL_CATALOG)
