"""The one LLM wrapper: every model call in the app goes through here (retries, timeout, tracing).

Provider is chosen by settings (LLM_PROVIDER): none | ollama | openai | anthropic.
- `none` (the default) means no model: features that need one report that they're unavailable.
- `ollama` runs locally and is free. `openai` also covers any OpenAI-compatible server (LM Studio, vLLM...).
- `anthropic` uses the official SDK, which resolves credentials itself (ANTHROPIC_API_KEY or an `ant` profile).
"""

import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2 as httpx

from app.settings import Settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    """The model call failed, or its answer couldn't be used."""


class LLMUnavailable(LLMError):
    """No provider is configured."""


@dataclass(frozen=True)
class LLMCall:
    model: str
    seconds: float
    ok: bool
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class LLM(ABC):
    model: str
    calls: list[LLMCall] = field(default_factory=list)  # trace, newest last

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"

    @abstractmethod
    def _complete(self, system: str, prompt: str) -> tuple[str, int | None, int | None]:
        """(text, input tokens, output tokens)"""

    def complete(self, system: str, prompt: str) -> str:
        start = time.perf_counter()
        try:
            text, tokens_in, tokens_out = self._complete(system, prompt)
        except Exception:
            self.calls.append(LLMCall(self.name, time.perf_counter() - start, ok=False))
            raise
        call = LLMCall(
            self.name, time.perf_counter() - start, ok=True, input_tokens=tokens_in, output_tokens=tokens_out
        )
        self.calls.append(call)
        log.info("llm %s: %.1fs, %s in / %s out tokens", self.name, call.seconds, tokens_in, tokens_out)
        return text

    def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        """Ask for a single JSON object and parse it."""
        text = self.complete(system + "\n\nRespond with a single JSON object and nothing else.", prompt)
        return parse_json(text)


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in a model reply, tolerating code fences and surrounding prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end < start:
        raise LLMError(f"no JSON object in model reply: {text[:200]!r}")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON in model reply: {exc}") from None
    if not isinstance(value, dict):
        raise LLMError("model reply is not a JSON object")
    return value


# --- HTTP providers (Ollama, OpenAI-compatible) ---------------------------------------------------


@dataclass
class _HttpLLM(LLM):
    base_url: str = ""
    api_key: str | None = None
    timeout: float = 120.0
    max_retries: int = 2
    max_tokens: int = 4096
    temperature: float | None = 0.0

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = self.base_url.rstrip("/") + path
        for attempt in range(self.max_retries + 1):
            try:
                r = httpx.post(url, json=body, headers=headers, timeout=self.timeout)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"{self.provider} unreachable at {self.base_url}: {exc}") from None
                self._sleep(attempt, None)
                continue
            if r.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                self._sleep(attempt, r.headers.get("retry-after"))
                continue
            if r.status_code >= 400:
                raise LLMError(f"{self.provider} returned {r.status_code}: {r.text[:300]}")
            data: dict[str, Any] = r.json()
            return data
        raise AssertionError("unreachable")

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None) -> None:
        try:
            delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        time.sleep(min(max(delay, 2**attempt + random.random()), 30))


@dataclass
class OllamaLLM(_HttpLLM):
    @property
    def provider(self) -> str:
        return "ollama"

    def _complete(self, system: str, prompt: str) -> tuple[str, int | None, int | None]:
        options: dict[str, Any] = {"num_predict": self.max_tokens}
        if self.temperature is not None:
            options["temperature"] = self.temperature
        data = self._post(
            "/api/chat",
            {
                "model": self.model,
                "stream": False,
                "options": options,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
        )
        return data["message"]["content"], data.get("prompt_eval_count"), data.get("eval_count")


@dataclass
class OpenAILLM(_HttpLLM):
    @property
    def provider(self) -> str:
        return "openai"

    def _complete(self, system: str, prompt: str) -> tuple[str, int | None, int | None]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        data = self._post("/chat/completions", body)
        usage = data.get("usage") or {}
        return (
            data["choices"][0]["message"]["content"] or "",
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )


# --- Anthropic (official SDK) ---------------------------------------------------------------------


@dataclass
class AnthropicLLM(LLM):
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 120.0
    max_retries: int = 2
    max_tokens: int = 4096
    _client: Any = None

    @property
    def provider(self) -> str:
        return "anthropic"

    def _complete(self, system: str, prompt: str) -> tuple[str, int | None, int | None]:
        import anthropic

        if self._client is None:
            # The SDK retries connection errors, 408/409/429 and 5xx with backoff, honouring retry-after.
            self._client = anthropic.Anthropic(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, max_retries=self.max_retries
            )
        try:
            # No temperature: the Messages API rejects sampling parameters.
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"anthropic unreachable: {exc}") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic returned {exc.status_code}: {exc.message}") from None
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMError(f"anthropic declined the request (category: {category})")
        text = "".join(block.text for block in response.content if block.type == "text")
        return text, response.usage.input_tokens, response.usage.output_tokens


# --- Tests ----------------------------------------------------------------------------------------


@dataclass
class ScriptedLLM(LLM):
    """Answers from a function of (system, prompt). For tests and offline evals."""

    reply: Callable[[str, str], str] = lambda system, prompt: ""

    @property
    def provider(self) -> str:
        return "scripted"

    def _complete(self, system: str, prompt: str) -> tuple[str, int | None, int | None]:
        return self.reply(system, prompt), None, None


def get_llm(settings: Settings) -> LLM | None:
    """The configured model, or None when LLM_PROVIDER=none."""
    s = settings
    if s.llm_provider == "none":
        return None
    assert s.llm_model is not None  # enforced by Settings
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else None
    common: dict[str, Any] = {"timeout": s.llm_timeout_seconds, "max_retries": s.llm_max_retries,
                              "max_tokens": s.llm_max_tokens}  # fmt: skip
    if s.llm_provider == "anthropic":
        return AnthropicLLM(model=s.llm_model, api_key=key, base_url=s.llm_base_url, **common)
    http: dict[str, Any] = {**common, "api_key": key, "temperature": s.llm_temperature}
    if s.llm_provider == "ollama":
        return OllamaLLM(model=s.llm_model, base_url=s.llm_base_url or "http://localhost:11434", **http)
    return OpenAILLM(model=s.llm_model, base_url=s.llm_base_url or "https://api.openai.com/v1", **http)


def require_llm(settings: Settings) -> LLM:
    llm = get_llm(settings)
    if llm is None:
        raise LLMUnavailable("no LLM configured: set LLM_PROVIDER and LLM_MODEL (see .env.example)")
    return llm
