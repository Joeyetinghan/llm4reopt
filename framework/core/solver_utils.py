"""Helpers for normalizing solver outputs across environments."""
from __future__ import annotations

from typing import Any, Sequence, Tuple

from .interfaces import BaseEnv
from .failure_taxonomy import attach_patchedit_failure
from .model import StructuredModel


class SolveFailureError(RuntimeError):
    """Structured solve failure carrying optional solver metadata."""

    def __init__(self, message: str, *, solve_meta: dict[str, Any] | None = None):
        super().__init__(message)
        self.solve_meta = dict(solve_meta or {})


def solve_with_metadata(env: BaseEnv, model: StructuredModel) -> Tuple[float, Any, dict]:
    """Call env.solve and normalize to (cost, solution, metadata)."""
    result = env.solve(model)

    if not isinstance(result, tuple):
        raise RuntimeError(f"Expected tuple solve result, got: {type(result)!r}")

    if len(result) == 3:
        cost, solution, meta = result
        return float(cost), solution, _coerce_meta(meta)

    if len(result) == 2:
        cost, solution = result
        meta = getattr(env, "last_solve_meta", None)
        return float(cost), solution, _coerce_meta(meta)

    raise RuntimeError(f"Unsupported solve result arity: {len(result)}")


def _coerce_meta(meta: Any) -> dict:
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def summarize_candidate_failures(
    failures: Sequence[BaseException],
    *,
    fallback: str = "No feasible patches found",
) -> RuntimeError:
    """Collapse repeated candidate-evaluation failures into one readable error."""

    unique_messages: list[str] = []
    for failure in failures:
        message = str(failure).strip() or failure.__class__.__name__
        if message not in unique_messages:
            unique_messages.append(message)

    if not unique_messages:
        return RuntimeError(fallback)

    chosen_kind = _select_patchedit_failure_kind(failures)
    if len(unique_messages) == 1:
        error = RuntimeError(unique_messages[0])
        if chosen_kind is not None:
            attach_patchedit_failure(error, kind=chosen_kind, detail=str(error))
        return error

    preview = "; ".join(unique_messages[:3])
    if len(unique_messages) > 3:
        preview += f"; ... ({len(unique_messages)} distinct failures)"
    error = RuntimeError(f"{fallback}: {preview}")
    if chosen_kind is not None:
        attach_patchedit_failure(error, kind=chosen_kind, detail=str(error))
    return error


def _select_patchedit_failure_kind(
    failures: Sequence[BaseException],
) -> str | None:
    priority = {
        "patch_application_failed": 1,
        "solve_failed": 2,
        "no_incumbent": 3,
    }
    chosen_kind: str | None = None
    chosen_priority = -1
    for failure in failures:
        kind = str(getattr(failure, "patchedit_failure_kind", "") or "").strip()
        if not kind:
            continue
        kind_priority = priority.get(kind, 0)
        if kind_priority > chosen_priority:
            chosen_kind = kind
            chosen_priority = kind_priority
    return chosen_kind
