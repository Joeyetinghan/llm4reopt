"""Exam block sequencing packaged problem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapter import ExamBlockSeqProblemAdapter

__all__ = ["ExamBlockSeqProblemAdapter"]


def __getattr__(name: str) -> Any:
    # The adapter is loaded on first use: framework.execution imports execution_labels from this
    # package, and the adapter imports problems.base, which imports framework.execution.
    if name == "ExamBlockSeqProblemAdapter":
        from .adapter import ExamBlockSeqProblemAdapter

        return ExamBlockSeqProblemAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
