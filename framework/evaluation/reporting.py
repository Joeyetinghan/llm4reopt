"""Reporting and evaluation helpers."""

from __future__ import annotations

import math
from typing import Any

from framework.core import EvaluationSummary, ProblemRunResult, ReoptResult


def infer_feasible(status: Any, objective: float | None = None) -> bool | None:
    finite_objective = (
        isinstance(objective, (int, float))
        and math.isfinite(float(objective))
    )
    if status is None:
        return finite_objective
    if isinstance(status, str):
        lowered = status.lower()
        if "infeasible" in lowered:
            return False
        if "optimal" in lowered:
            return True
        if "time_limit" in lowered:
            return True if finite_objective else False if objective is not None else None
        if "feasible" in lowered:
            return True
    if isinstance(status, (int, float)):
        if int(status) == 3:
            return False
        if int(status) == 2:
            return True
        if int(status) == 9:
            return True if finite_objective else False if objective is not None else None
    return None if objective is None else finite_objective


def build_evaluation_summary(
    *,
    objective: float | None,
    solve_meta: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> EvaluationSummary:
    details = dict(details or {})
    return EvaluationSummary(
        status=solve_meta.get("status"),
        runtime=_coerce_float(solve_meta.get("runtime") or solve_meta.get("solve_time")),
        objective=objective,
        feasible=infer_feasible(solve_meta.get("status"), objective),
        checks={"has_solution": objective is not None},
        details={**details, **solve_meta},
    )


def print_run_report(result: ProblemRunResult) -> None:
    print(f"=== ReOpt Problem: {result.problem.name} ({result.problem.problem_id}) ===")
    print(f"Mode: {result.mode}")
    print(f"Base objective: {result.base_objective:.4f}")
    _print_solve_meta("Base solve", result.base_solve_meta)

    if not result.steps:
        return

    if result.mode == "sequence":
        print(f"Steps: {len(result.steps)}")
        for idx, step in enumerate(result.steps, start=1):
            print(f"- Step {idx}: {step.delta_request.text}")
            print(f"  Strategy: {step.strategy.value}")
            print(f"  Objective: {step.objective:.4f}")
            print(f"  Report: {step.change_report}")
    else:
        step = result.steps[0]
        print(f"Delta: {step.delta_request.text}")
        print(f"Strategy: {step.strategy.value}")
        print(f"New objective: {step.objective:.4f}")
        _print_solve_meta("New solve", step.solve_meta)
        print(f"Report: {step.change_report}")


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _print_solve_meta(label: str, meta: dict[str, Any]) -> None:
    if not meta:
        return
    parts = []
    if meta.get("status") is not None:
        parts.append(f"status={meta['status']}")
    runtime = _coerce_float(meta.get("runtime") or meta.get("solve_time"))
    if runtime is not None:
        parts.append(f"runtime={runtime:.2f}s")
    gap = _coerce_float(meta.get("mip_gap") or meta.get("gap"))
    if gap is not None:
        parts.append(f"gap={gap:.6f}")
    if parts:
        print(f"{label}: " + ", ".join(parts))
