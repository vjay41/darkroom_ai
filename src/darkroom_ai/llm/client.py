"""OpenAI-compatible chat client for a model server on THIS computer.

The address comes from the model profile and must be a loopback address; all traffic goes through
net.loopback.LocalHttp, which ignores proxy settings. Nothing here knows a model name: the profile
supplies it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from darkroom_ai.config import ModelProfile
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.net.loopback import LocalHttp, LocalHttpError

Message = dict[str, Any]
VISION_ERROR_HINTS = ("image", "vision", "mmproj", "multimodal")


@dataclass(frozen=True)
class RawToolCall:
    """A tool call exactly as the server returned it (arguments still a JSON string)."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ChatReply:
    content: str
    tool_calls: tuple[RawToolCall, ...] = ()
    seconds: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""


class LlmClient(Protocol):
    def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> ChatReply: ...

    def models(self) -> list[str]: ...


class LocalLlmClient:
    def __init__(self, profile: ModelProfile, http: LocalHttp | None = None) -> None:
        self._profile = profile
        self._http = http or LocalHttp(profile.base_url, timeout_s=profile.timeout_s)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, payload: object | None = None) -> Any:
        try:
            status, data = self._http.request_json(method, path, payload)
        except LocalHttpError as exc:
            raise DarkroomError(
                ErrorCode.LLM_NOT_REACHABLE,
                f"The local model server at {self._profile.base_url} did not answer ({exc}).",
                details={"base_url": self._profile.base_url},
            ) from exc
        if status >= 400:
            text = json.dumps(data) if not isinstance(data, str) else data
            sent_image = payload is not None and '"image_url"' in json.dumps(payload)
            if sent_image and any(hint in text.lower() for hint in VISION_ERROR_HINTS):
                raise DarkroomError(
                    ErrorCode.LLM_NO_VISION,
                    f"The model '{self._profile.model}' rejected the image: {text[:300]}",
                    details={"status": status},
                )
            raise DarkroomError(
                ErrorCode.LLM_REQUEST_FAILED,
                f"The local model server answered with an error ({status}): {text[:300]}",
                details={"status": status, "model": self._profile.model},
            )
        return data

    def models(self) -> list[str]:
        data = self._request("GET", "models")
        items = data.get("data", []) if isinstance(data, dict) else []
        return [str(item.get("id")) for item in items if isinstance(item, dict)]

    def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> ChatReply:
        payload: dict[str, Any] = {
            "model": self._profile.model,
            "messages": list(messages),
            "temperature": self._profile.temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        started = time.monotonic()
        data = self._request("POST", "chat/completions", payload)
        seconds = time.monotonic() - started
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DarkroomError(
                ErrorCode.LLM_REQUEST_FAILED,
                "The local model server returned an answer without a message.",
            ) from exc
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {})
            calls.append(
                RawToolCall(
                    call_id=str(call.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )
        return ChatReply(
            content=str(message.get("content") or ""),
            tool_calls=tuple(calls),
            seconds=round(seconds, 2),
            usage=dict(data.get("usage") or {}),
            finish_reason=str(choice.get("finish_reason") or ""),
        )
