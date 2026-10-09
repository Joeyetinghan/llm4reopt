"""Shared LLM defaults for the repo."""
from __future__ import annotations

import os


def _env_optional_float(name: str, default: float | None = None) -> float | None:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip()
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float for {name}: {value!r}") from exc


def _env_optional_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip()
    return cleaned or None


# Change the repo-wide default model here. An env override is also supported for
# temporary experiments without editing code.
DEFAULT_LLM_MODEL = os.getenv("REOPT_DEFAULT_LLM_MODEL", "gpt-4.1-mini")

# Leave sampling unset by default so provider/model defaults apply. Set these
# env vars when an experiment intentionally wants explicit global overrides.
DEFAULT_LLM_TEMPERATURE = _env_optional_float("REOPT_DEFAULT_LLM_TEMPERATURE")
DEFAULT_LLM_TOP_P = _env_optional_float("REOPT_DEFAULT_LLM_TOP_P")
DEFAULT_OPENAI_REASONING_EFFORT = _env_optional_str(
    "REOPT_DEFAULT_OPENAI_REASONING_EFFORT",
    "medium",
)
DEFAULT_AZURE_DEPLOYMENT_PREFIX = os.getenv(
    "OPENAI_AZURE_DEPLOYMENT_PREFIX",
    "",
)


def canonical_openai_chat_model_name(model_name: str | None) -> str:
    """Normalize OpenAI model names across raw, provider-prefixed, and Azure deployment forms."""

    normalized = (model_name or "").strip().lower()
    if not normalized:
        return ""
    if "/" in normalized:
        normalized = normalized.split("/")[-1]
    deployment_prefix = os.getenv(
        "OPENAI_AZURE_DEPLOYMENT_PREFIX",
        DEFAULT_AZURE_DEPLOYMENT_PREFIX,
    ).strip().lower()
    if deployment_prefix and normalized.startswith(deployment_prefix):
        normalized = normalized[len(deployment_prefix) :]
    return normalized


def azure_openai_deployment_name(
    model_name: str | None,
    *,
    deployment_prefix: str | None = None,
) -> str:
    """Resolve the Azure deployment name for an OpenAI chat model."""

    canonical = canonical_openai_chat_model_name(model_name)
    if not canonical:
        return ""
    prefix = (
        deployment_prefix
        if deployment_prefix is not None
        else os.getenv(
            "OPENAI_AZURE_DEPLOYMENT_PREFIX",
            DEFAULT_AZURE_DEPLOYMENT_PREFIX,
        )
    )
    cleaned_prefix = (prefix or "").strip()
    return f"{cleaned_prefix}{canonical}" if cleaned_prefix else canonical


def openai_reasoning_effort(model_name: str | None) -> str | None:
    """Return the repo's explicit reasoning effort for GPT-5-family models."""

    normalized = canonical_openai_chat_model_name(model_name)
    if not normalized.startswith("gpt-5"):
        return None
    return _env_optional_str(
        "REOPT_DEFAULT_OPENAI_REASONING_EFFORT",
        DEFAULT_OPENAI_REASONING_EFFORT,
    )


def openai_supports_custom_sampling(
    model_name: str | None,
    *,
    reasoning_effort: str | None = None,
) -> bool:
    """Whether the repo should send explicit temperature/top_p for the given OpenAI model."""

    normalized = canonical_openai_chat_model_name(model_name)
    if normalized.startswith(("gpt-4.1", "gpt-5")):
        return False
    if not normalized.startswith("gpt-5"):
        return True

    effort = (reasoning_effort or openai_reasoning_effort(model_name) or "").strip().lower()
    if normalized.startswith(("gpt-5.1", "gpt-5.2")):
        return effort == "none"
    return False
