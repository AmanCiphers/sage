import json
import re
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from app.inference.interface import InferenceProvider
from app.inference.models import (
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    StreamDelta,
)

from .config import ProviderConfig
from .errors import (
    ProviderAPIError,
    ProviderResponseError,
    ProviderTimeoutError,
)

# Chat-template tokens (e.g. `<|channel|>commentary`) sometimes leak into
# tool-call fields from reasoning models — never let them reach a registry
# lookup as part of a tool name.
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|<>]*\|>")
_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]")


class OpenAICompatibleProvider(InferenceProvider):
    def __init__(
        self,
        config: ProviderConfig,
        metadata: ModelMetadata,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._metadata = metadata
        self._client = client

    @property
    def metadata(self) -> ModelMetadata:
        return self._metadata

    def _payload(self, request: InferenceRequest) -> dict:
        payload = {
            "model": self.metadata.model_id,
            "messages": request.messages,
            "temperature": request.temperature,
        }

        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort

        if request.tools:
            payload["tools"] = request.tools

        return payload

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = self._payload(request)
        headers = self._headers

        start = time.perf_counter()

        try:
            if self._client is not None:
                response = await self._client.post(
                    url,
                    json=payload,
                    headers=headers,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.config.timeout
                ) as client:
                    response = await client.post(
                        url,
                        json=payload,
                        headers=headers,
                    )

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Provider request timed out: {self.metadata.model_id}"
            ) from exc

        latency_ms = (time.perf_counter() - start) * 1000

        if response.status_code >= 400:
            raise ProviderAPIError(
                f"Provider returned HTTP {response.status_code}: "
                f"{response.text}"
            )

        try:
            data = response.json()
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls")

            if not content.strip() and not tool_calls:
                raise ProviderResponseError(
                    f"Provider returned no assistant content: "
                    f"{self.metadata.model_id}"
                )

        except ProviderResponseError:
            raise

        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                f"Invalid response from provider: {self.metadata.model_id}"
            ) from exc

        usage = data.get("usage", {})

        return InferenceResponse(
            content=content,
            model=data.get("model", self.metadata.model_id),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
            tool_calls=[_sanitize_tool_call(tc) for tc in tool_calls]
            if tool_calls
            else None,
        )

    async def stream(
        self,
        request: InferenceRequest,
    ) -> AsyncIterator[StreamDelta]:
        """SSE streaming: content chunks live; tool_calls accumulated
        from fragments and yielded complete at the end."""

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = {**self._payload(request), "stream": True, "stream_options": {"include_usage": True}}
        headers = self._headers

        tool_fragments: dict[int, dict] = {}
        usage = None
        start = time.perf_counter()

        if self._client is not None:
            client_ctx = _client_scope(self._client, self.config.timeout)
        else:
            client_ctx = httpx.AsyncClient(timeout=self.config.timeout)

        try:
            async with client_ctx as client, client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise ProviderAPIError(
                        f"Provider returned HTTP {response.status_code}: "
                        f"{body.decode(errors='replace')}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    data = line[len("data:"):].strip()

                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        # Could be the final usage chunk with no choices
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        continue

                    delta = choices[0].get("delta") or {}

                    content = delta.get("content")
                    if content:
                        yield StreamDelta(content=content)

                    for fragment in delta.get("tool_calls") or []:
                        index = fragment.get("index", 0)
                        accumulator = tool_fragments.setdefault(
                            index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {
                                    "name": "",
                                    "arguments": "",
                                },
                            },
                        )
                        if fragment.get("id"):
                            accumulator["id"] = fragment["id"]

                        function = fragment.get("function") or {}
                        if function.get("name"):
                            accumulator["function"]["name"] += function["name"]
                        if function.get("arguments"):
                            accumulator["function"]["arguments"] += function[
                                "arguments"
                            ]

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Provider request timed out: {self.metadata.model_id}"
            ) from exc

        latency_ms = (time.perf_counter() - start) * 1000

        if tool_fragments:
            tool_calls = [
                _sanitize_tool_call(_ensure_tool_call_id(tool_fragments[index]))
                for index in sorted(tool_fragments)
            ]
            yield StreamDelta(tool_calls=tool_calls)

        if usage:
            yield StreamDelta(
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                latency_ms=latency_ms,
            )


def _sanitize_tool_call(tool_call: dict) -> dict:
    """Strip chat-template junk from a tool call.

    The real name always precedes the leak: `start_task` + a streamed
    `<|channel|>commentary` fragment. Everything from the first `<|` on
    is junk (the trailing `commentary` outside the token too). Arguments
    are salvaged only when the slice between the first `{` and the last
    `}` parses as JSON — otherwise they are left untouched.
    """

    function = tool_call.get("function")
    if not isinstance(function, dict):
        return tool_call

    name = function.get("name")
    if isinstance(name, str):
        name = name.split("<|", 1)[0]
        name = _SPECIAL_TOKEN_RE.sub("", name)
        function["name"] = _TOOL_NAME_RE.sub("", name)

    arguments = function.get("arguments")
    if isinstance(arguments, str):
        cleaned = _SPECIAL_TOKEN_RE.sub("", arguments)
        candidate = _first_json_object(cleaned)
        if candidate is not None:
            cleaned = candidate
        function["arguments"] = cleaned

    return tool_call


def _first_json_object(text: str) -> str | None:
    """First balanced `{...}` slice (string-aware), validated as JSON."""

    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    json.loads(candidate)
                except ValueError:
                    return None
                return candidate

    return None


def _ensure_tool_call_id(tool_call: dict) -> dict:
    if not tool_call.get("id"):
        tool_call["id"] = f"call_{uuid.uuid4().hex[:12]}"
    return tool_call


class _client_scope:
    """Reuse a caller-provided httpx client (tests) without closing it."""

    def __init__(self, client: httpx.AsyncClient, timeout: float) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args) -> None:
        return None
