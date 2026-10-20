import json

import httpx
import pytest

from app.inference.models import (
    InferenceRequest,
    ModelCapabilities,
    ModelMetadata,
)
from app.inference.providers.config import ProviderConfig
from app.inference.providers.errors import (
    ProviderAPIError,
    ProviderTimeoutError,
)
from app.inference.providers.openai_compatible import (
    OpenAICompatibleProvider,
)


@pytest.mark.asyncio
async def test_provider_sends_chat_completion_request():
    request_data = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        request_data["url"] = str(request.url)
        request_data["headers"] = dict(request.headers)
        request_data["json"] = request.content

        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": "Hello from model",
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key="test-key",
                base_url="https://example.com/v1",
            ),
            metadata=ModelMetadata(
                provider="test",
                model_id="test-model",
                context_window=4096,
                capabilities=ModelCapabilities(),
            ),
            client=client,
        )

        response = await provider.generate(
            InferenceRequest(
                messages=[
                    {"role": "user", "content": "Hello"},
                ],
            )
        )

    assert response.content == "Hello from model"
    assert response.model == "test-model"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.latency_ms is not None


@pytest.mark.asyncio
async def test_provider_sends_authentication_header():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]

        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": "OK",
                        }
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key="secret-key",
                base_url="https://example.com/v1",
            ),
            metadata=ModelMetadata(
                provider="test",
                model_id="test-model",
                context_window=4096,
            ),
            client=client,
        )

        await provider.generate(
            InferenceRequest(
                messages=[
                    {"role": "user", "content": "Hello"},
                ],
            )
        )

    assert captured["authorization"] == "Bearer secret-key"


@pytest.mark.asyncio
async def test_provider_raises_api_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": "server error"},
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key="test-key",
                base_url="https://example.com/v1",
            ),
            metadata=ModelMetadata(
                provider="test",
                model_id="test-model",
                context_window=4096,
            ),
            client=client,
        )

        with pytest.raises(ProviderAPIError):
            await provider.generate(
                InferenceRequest(
                    messages=[
                        {"role": "user", "content": "Hello"},
                    ],
                )
            )


@pytest.mark.asyncio
async def test_provider_raises_timeout_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "request timed out",
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key="test-key",
                base_url="https://example.com/v1",
            ),
            metadata=ModelMetadata(
                provider="test",
                model_id="test-model",
                context_window=4096,
            ),
            client=client,
        )

        with pytest.raises(ProviderTimeoutError):
            await provider.generate(
                InferenceRequest(
                    messages=[
                        {"role": "user", "content": "Hello"},
                    ],
                )
            )


@pytest.mark.asyncio
async def test_generate_sanitizes_polluted_tool_call_names():
    """Non-streaming path: chat-template junk in name/arguments is
    stripped before the response reaches the loop."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "start_task<|channel|>commentary",
                                        "arguments": (
                                            "<|channel|>commentary"
                                            '{"objective": "x"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key="test-key",
                base_url="https://example.com/v1",
            ),
            metadata=ModelMetadata(
                provider="test",
                model_id="test-model",
                context_window=4096,
            ),
            client=client,
        )

        response = await provider.generate(
            InferenceRequest(
                messages=[{"role": "user", "content": "hi"}],
            )
        )

    assert response.tool_calls is not None
    function = response.tool_calls[0]["function"]
    assert function["name"] == "start_task"
    assert json.loads(function["arguments"]) == {"objective": "x"}
