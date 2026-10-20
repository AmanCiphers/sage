from app.inference.models import ModelCapabilities, ModelMetadata


# Primary chat model: Nebius DeepSeek-V4-Flash — fast, cheap, tool-capable.
# Reasoning=False keeps compaction/summary turns on the reasoning model.
NEBIUS_MODELS = [
    ModelMetadata(
        provider="nebius",
        model_id="deepseek-ai/DeepSeek-V4-Flash-0731",
        context_window=131072,
        capabilities=ModelCapabilities(
            tool_calling=True,
            reasoning=False,
            streaming=True,
        ),
        latency_class="fast",
        cost_class="low",
        priority=90,
        available=True,
    ),
]

# Fallback (and reasoning work like compaction): NVIDIA gpt-oss-20b.
NVIDIA_MODELS = [
    ModelMetadata(
        provider="nvidia",
        model_id="openai/gpt-oss-20b",
        context_window=131072,
        capabilities=ModelCapabilities(
            tool_calling=True,
            reasoning=True,
            streaming=True,
        ),
        latency_class="fast",
        cost_class="low",
        priority=80,
        available=True
    ),
]
