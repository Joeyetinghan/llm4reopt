"""LLM client abstractions: Gemini and Azure OpenAI implementations."""
from __future__ import annotations

import fcntl
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List

from ..core.model import StructuredModel
from .config import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_LLM_TOP_P,
    azure_openai_deployment_name,
    openai_reasoning_effort,
    openai_supports_custom_sampling,
)


ChatMessage = Dict[str, str]


def _load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()

_AZURE_ENDPOINT = ""
_AZURE_API_VERSION = "2024-12-01-preview"
_AZURE_DEPLOYMENT_PREFIX = ""

OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4")
_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_TRANSIENT_ERROR_MARKERS = (
    "rate limit",
    "too many requests",
    "temporarily unavailable",
    "temporarily overloaded",
    "service unavailable",
    "unavailable",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection error",
    "server error",
    "internal error",
    "try again later",
)
_ANALYZE_DELTA_SCHEMA_EXAMPLE = {
    "relevant_components": ["list of constraint/objective identifiers"],
    "affected_sets": {"set_name": ["idx", "..."]},
    "edit_summary": "short free-form summary of the requested edit",
    "event_type": "optional legacy tag if clearly useful",
    "intention": "optional legacy tag if clearly useful",
}


def is_openai_model(name: str) -> bool:
    """Heuristic: model name starts with a known OpenAI prefix."""
    lower = name.strip().lower()
    return any(lower.startswith(p) for p in OPENAI_MODEL_PREFIXES)


def _structured_model_context(model: StructuredModel) -> Dict[str, Any]:
    return {
        "parameters": model.parameters,
        "variables": list(model.variables.keys()),
        "constraints": list(model.constraints.keys()),
        "objectives": list(model.objectives.keys()),
    }


def _extract_json_blob(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain JSON object")
    return text[start : end + 1]


def _build_analysis_prompt(delta_text: str, model: StructuredModel) -> str:
    schema_example = json.dumps(_ANALYZE_DELTA_SCHEMA_EXAMPLE, indent=2)
    model_context = json.dumps(_structured_model_context(model))
    return (
        "You are an expert operations-research engineer helping classify optimization changes.\n"
        "Given the user's delta text and a summary of the model, respond with a STRICT JSON\n"
        "object using the following lightweight schema:\n"
        f"{schema_example}\n"
        "Return JSON only with no Markdown, comments, or additional text.\n"
        f"Delta text: {delta_text}\n"
        f"Model context JSON: {model_context}\n"
    )


def _parse_json_response(text: str, *, provider: str) -> Dict[str, Any]:
    try:
        json_blob = _extract_json_blob(text)
        return json.loads(json_blob)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to parse {provider} response: {text}") from exc


def _llm_retry_max_attempts() -> int:
    raw = os.getenv("REOPT_LLM_MAX_ATTEMPTS", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _llm_retry_base_delay_seconds() -> float:
    raw = os.getenv("REOPT_LLM_RETRY_BASE_DELAY_SECONDS", "2.0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2.0


def _gemini_min_interval_seconds() -> float:
    raw = os.getenv("REOPT_GEMINI_MIN_INTERVAL_SECONDS", "0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _gemini_rate_limit_state_dir() -> Path:
    raw = os.getenv("REOPT_GEMINI_RATE_LIMIT_STATE_DIR", "tmp/llm_rate_limits")
    return Path(raw).expanduser().resolve()


def _gemini_rate_limit_scope() -> str:
    raw = os.getenv("REOPT_GEMINI_RATE_LIMIT_SCOPE", "model")
    cleaned = raw.strip().lower()
    return cleaned or "model"


def _gemini_rate_limit_state_path(model_name: str) -> Path:
    scope = _gemini_rate_limit_scope()
    key = "global" if scope == "global" else (model_name if scope == "model" else scope)
    slug = "".join(
        char if char.isalnum() else "_"
        for char in str(key).strip().lower()
    ).strip("_")
    if not slug:
        slug = "gemini"
    return _gemini_rate_limit_state_dir() / f"{slug}.json"


def _gemini_wait_for_slot(model_name: str) -> None:
    min_interval = _gemini_min_interval_seconds()
    if min_interval <= 0:
        return

    state_path = _gemini_rate_limit_state_path(model_name)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)

        last_request_at = 0.0
        if state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                last_request_at = float(payload.get("last_request_at") or 0.0)
            except Exception:
                last_request_at = 0.0

        now = time.time()
        wait_seconds = max(0.0, (last_request_at + min_interval) - now)
        if wait_seconds > 0:
            print(
                f"[gemini rate limit] {model_name} waiting {wait_seconds:.1f}s "
                "before the next request"
            )
            time.sleep(wait_seconds)

        state_path.write_text(
            json.dumps(
                {
                    "model_name": model_name,
                    "last_request_at": time.time(),
                    "min_interval_seconds": min_interval,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _extract_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _is_transient_llm_error(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code in _TRANSIENT_STATUS_CODES:
        return True

    message = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)


def _call_with_retry(fn, *, label: str):
    max_attempts = _llm_retry_max_attempts()
    base_delay = _llm_retry_base_delay_seconds()
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_transient_llm_error(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(
                f"[llm retry] {label} attempt {attempt}/{max_attempts} failed with "
                f"{exc.__class__.__name__}; retrying in {delay:.1f}s"
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"LLM retry wrapper reached unexpected empty state for {label}")


class BaseLLMClient(ABC):
    @abstractmethod
    def chat(self, messages: List[ChatMessage]) -> str:
        """Send a chat-style prompt to the LLM and return the raw text response."""

    def analyze_delta(self, delta_text: str, model: StructuredModel) -> Dict[str, Any]:
        """Optional helper for specialized flows."""
        raise NotImplementedError


class GeminiLLMClient(BaseLLMClient):
    """Google Gemini wrapper that produces structured patch plans."""

    def __init__(
        self,
        model_name: str = DEFAULT_LLM_MODEL,
        api_key: str | None = None,
        *,
        temperature: float | None = DEFAULT_LLM_TEMPERATURE,
        top_p: float | None = DEFAULT_LLM_TOP_P,
    ):
        from google import genai
        from google.genai import types as genai_types

        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise RuntimeError("Gemini API key missing. Set GEMINI_API_KEY/GOOGLE_API_KEY or pass api_key.")
        self._client = genai.Client(api_key=self.api_key)
        self._types = genai_types
        self._model_name = model_name
        self._generation_config_kwargs: dict[str, Any] = {}
        if temperature is not None:
            self._generation_config_kwargs["temperature"] = temperature
        if top_p is not None:
            self._generation_config_kwargs["topP"] = top_p
        self._generation_config = (
            genai_types.GenerateContentConfig(**self._generation_config_kwargs)
            if self._generation_config_kwargs
            else None
        )

    def chat(self, messages: List[ChatMessage]) -> str:
        system_instruction, contents = self._messages_to_gemini_request(messages)
        return self._generate_text(
            contents=contents,
            system_instruction=system_instruction,
            label=f"gemini:{self._model_name}",
            empty_error="Gemini response empty",
        )

    def analyze_delta(self, delta_text: str, model: StructuredModel) -> Dict[str, Any]:
        prompt = _build_analysis_prompt(delta_text, model)
        text = self._generate_text(
            contents=self._user_contents(prompt),
            system_instruction=None,
            label=f"gemini:{self._model_name}:analyze_delta",
            empty_error="Gemini response empty; cannot parse analysis",
        )
        return _parse_json_response(text, provider="Gemini")

    @staticmethod
    def _extract_text(response: Any) -> str | None:
        text = getattr(response, "text", None)
        if text:
            return text
        assembled: List[str] = []
        for candidate in getattr(response, "candidates", []):
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", []) if content is not None else []
            for part in parts:
                if hasattr(part, "text") and part.text:
                    assembled.append(part.text)
        return "".join(assembled) if assembled else None

    def _generate_text(
        self,
        *,
        contents: list[Any],
        system_instruction: str | None,
        label: str,
        empty_error: str,
    ) -> str:
        config = self._build_generation_config(system_instruction=system_instruction)
        response = _call_with_retry(
            lambda: self._generate_content(contents=contents, config=config),
            label=label,
        )
        text = self._extract_text(response)
        if not text:
            raise RuntimeError(empty_error)
        return text

    def _generate_content(self, *, contents: list[Any], config: Any | None):
        _gemini_wait_for_slot(self._model_name)
        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "contents": contents,
        }
        if config is not None:
            kwargs["config"] = config
        return self._client.models.generate_content(**kwargs)

    def _build_generation_config(self, *, system_instruction: str | None) -> Any | None:
        if system_instruction is None:
            return self._generation_config
        config_kwargs = dict(self._generation_config_kwargs)
        if system_instruction:
            config_kwargs["systemInstruction"] = system_instruction
        if not config_kwargs:
            return None
        return self._types.GenerateContentConfig(**config_kwargs)

    def _messages_to_gemini_request(
        self,
        messages: List[ChatMessage],
    ) -> tuple[str | None, list[Any]]:
        system_parts: list[str] = []
        contents: list[Any] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            content = str(message.get("content") or "")
            if role == "system":
                if content:
                    system_parts.append(content)
                continue
            if not content:
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append(self._content(role=gemini_role, text=content))
        if not contents:
            contents = self._user_contents("")
        system_instruction = "\n\n".join(system_parts).strip() or None
        return system_instruction, contents

    def _user_contents(self, text: str) -> list[Any]:
        return [self._content(role="user", text=text)]

    def _content(self, *, role: str, text: str) -> Any:
        return self._types.Content(
            role=role,
            parts=[self._types.Part.from_text(text=text)],
        )


class OpenAIAzureLLMClient(BaseLLMClient):
    """OpenAI chat client.

    Uses Azure OpenAI when ``OPENAI_AZURE_ENDPOINT`` is set (deployment names are
    ``OPENAI_AZURE_DEPLOYMENT_PREFIX`` + model name) and the public OpenAI API otherwise.
    """

    def __init__(
        self,
        model_name: str = "gpt-4.1",
        api_key: str | None = None,
        endpoint: str | None = None,
        api_version: str | None = None,
        deployment_prefix: str | None = None,
        temperature: float | None = DEFAULT_LLM_TEMPERATURE,
        top_p: float | None = DEFAULT_LLM_TOP_P,
    ):
        import openai

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "OpenAI API key missing. Set OPENAI_API_KEY or pass api_key."
            )
        self._endpoint = endpoint or os.getenv(
            "OPENAI_AZURE_ENDPOINT", _AZURE_ENDPOINT,
        )
        self._api_version = api_version or os.getenv(
            "OPENAI_AZURE_API_VERSION", _AZURE_API_VERSION,
        )
        prefix = deployment_prefix or os.getenv(
            "OPENAI_AZURE_DEPLOYMENT_PREFIX", _AZURE_DEPLOYMENT_PREFIX,
        )
        self._deployment_name = azure_openai_deployment_name(
            model_name,
            deployment_prefix=prefix,
        )
        self._model_name = model_name
        self._temperature = temperature
        self._top_p = top_p
        self._reasoning_effort = openai_reasoning_effort(model_name)
        self._supports_custom_sampling = openai_supports_custom_sampling(
            model_name,
            reasoning_effort=self._reasoning_effort,
        )

        if self._endpoint:
            self._client = openai.AzureOpenAI(
                api_version=self._api_version,
                azure_endpoint=self._endpoint,
                api_key=self.api_key,
            )
        else:
            self._client = openai.OpenAI(api_key=self.api_key)

    def _chat_create_kwargs(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self._deployment_name,
            "messages": messages,
        }
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        if self._supports_custom_sampling:
            if self._temperature is not None:
                kwargs["temperature"] = self._temperature
            if self._top_p is not None:
                kwargs["top_p"] = self._top_p
        return kwargs

    def chat(self, messages: List[ChatMessage]) -> str:
        response = _call_with_retry(
            lambda: self._client.chat.completions.create(
                **self._chat_create_kwargs(messages),  # type: ignore[arg-type]
            ),
            label=f"openai:{self._model_name}",
        )
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("OpenAI response empty")
        return text

    def analyze_delta(self, delta_text: str, model: StructuredModel) -> Dict[str, Any]:
        prompt = _build_analysis_prompt(delta_text, model)
        response = _call_with_retry(
            lambda: self._client.chat.completions.create(
                **self._chat_create_kwargs([{"role": "user", "content": prompt}]),
            ),
            label=f"openai:{self._model_name}:analyze_delta",
        )
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("OpenAI response empty; cannot parse analysis")
        return _parse_json_response(text, provider="OpenAI")


def create_llm_client(
    model_name: str,
    api_key: str | None = None,
) -> BaseLLMClient:
    """Auto-detect provider from model name and return the right client."""
    if is_openai_model(model_name):
        return OpenAIAzureLLMClient(model_name=model_name, api_key=api_key)
    return GeminiLLMClient(model_name=model_name, api_key=api_key)
