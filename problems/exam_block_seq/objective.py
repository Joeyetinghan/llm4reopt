"""Shared helpers for materializing exam LP objective coefficients."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Mapping

from framework.core import StructuredModel

if TYPE_CHECKING:
    import gurobipy as gp


_X_PATTERN = re.compile(r"^x\[(\d+),(\d+),(\d+),(\d+)\]$")
_Z_PATTERN = re.compile(r"^z\[(\d+),(\d+),(\d+),(\d+)\]$")


def refresh_exam_lp_objective_from_structured(
    model: "gp.Model",
    structured: StructuredModel,
    *,
    default_weights: Mapping[str, float] | None = None,
) -> int:
    return refresh_exam_lp_objective(
        model,
        weights=_weights_from_structured(structured, default_weights),
        pair_counts=_merged_count_map(
            structured.parameters.get("pair_counts"),
            structured.parameters.get("p"),
            arity=2,
        ),
        triplet_counts=_merged_count_map(
            structured.parameters.get("triplet_counts"),
            structured.parameters.get("t"),
            arity=3,
        ),
        triple_day_start=_slot_set(structured.parameters.get("triple_day_start")),
        triple_24_start=_slot_set(structured.parameters.get("triple_24_start")),
        eve_morn_start=_slot_set(structured.parameters.get("eve_morn_start")),
        other_b2b_start=_slot_set(structured.parameters.get("other_b2b_start")),
    )


def refresh_exam_lp_objective_from_state(
    model: "gp.Model",
    state: Mapping[str, Any],
) -> int:
    return refresh_exam_lp_objective(
        model,
        weights=_weights_from_state(state),
        pair_counts=_coerce_count_map(state.get("pair_counts"), arity=2),
        triplet_counts=_coerce_count_map(state.get("triplet_counts"), arity=3),
        triple_day_start=_slot_set(state.get("triple_day_start")),
        triple_24_start=_slot_set(state.get("triple_24_start")),
        eve_morn_start=_slot_set(state.get("eve_morn_start")),
        other_b2b_start=_slot_set(state.get("other_b2b_start")),
    )


def refresh_exam_lp_objective(
    model: "gp.Model",
    *,
    weights: Mapping[str, float],
    pair_counts: Mapping[tuple[int, int], float],
    triplet_counts: Mapping[tuple[int, int, int], float],
    triple_day_start: set[int],
    triple_24_start: set[int],
    eve_morn_start: set[int],
    other_b2b_start: set[int],
) -> int:
    nonzero_coeffs = 0
    alpha = float(weights.get("alpha", 0.0))
    beta = float(weights.get("beta", 0.0))
    gamma1 = float(weights.get("gamma1", 0.0))
    gamma2 = float(weights.get("gamma2", 0.0))
    delta = float(weights.get("delta", 0.0))

    for variable in model.getVars():
        coeff = _x_objective_coeff(
            variable.VarName,
            alpha=alpha,
            beta=beta,
            gamma1=gamma1,
            gamma2=gamma2,
            pair_counts=pair_counts,
            triplet_counts=triplet_counts,
            triple_day_start=triple_day_start,
            triple_24_start=triple_24_start,
            eve_morn_start=eve_morn_start,
            other_b2b_start=other_b2b_start,
        )
        if coeff is None:
            coeff = _z_objective_coeff(
                variable.VarName,
                delta=delta,
                triplet_counts=triplet_counts,
            )
        if coeff is None:
            coeff = 0.0

        variable.Obj = coeff
        if abs(coeff) > 1e-12:
            nonzero_coeffs += 1

    model.update()
    return nonzero_coeffs


def _weights_from_structured(
    structured: StructuredModel,
    default_weights: Mapping[str, float] | None,
) -> dict[str, float]:
    return {
        name: _weight_from_structured(structured, name, (default_weights or {}).get(name, 0.0))
        for name in ("alpha", "beta", "gamma1", "gamma2", "delta")
    }


def _weight_from_structured(structured: StructuredModel, name: str, default: float) -> float:
    value = structured.parameters.get(name)
    if isinstance(value, (int, float)):
        return float(value)
    objective = structured.objectives.get(name)
    if objective is not None:
        return float(objective.weight)
    return float(default)


def _weights_from_state(state: Mapping[str, Any]) -> dict[str, float]:
    weights = dict(state.get("weights") or {})
    return {
        "alpha": float(weights.get("alpha", 0.0)),
        "beta": float(weights.get("beta", 0.0)),
        "gamma1": float(weights.get("gamma1", 0.0)),
        "gamma2": float(weights.get("gamma2", 0.0)),
        "delta": float(weights.get("delta", 0.0)),
    }


def _x_objective_coeff(
    var_name: str,
    *,
    alpha: float,
    beta: float,
    gamma1: float,
    gamma2: float,
    pair_counts: Mapping[tuple[int, int], float],
    triplet_counts: Mapping[tuple[int, int, int], float],
    triple_day_start: set[int],
    triple_24_start: set[int],
    eve_morn_start: set[int],
    other_b2b_start: set[int],
) -> float | None:
    match = _X_PATTERN.match(var_name)
    if match is None:
        return None

    i, j, k, slot = (int(match.group(idx)) for idx in range(1, 5))
    triplet = float(triplet_counts.get((i, j, k), 0.0))
    pair = float(pair_counts.get((i, j), 0.0))

    coeff = 0.0
    if slot in triple_day_start:
        coeff += alpha * triplet
    if slot in triple_24_start:
        coeff += beta * triplet
    if slot in eve_morn_start:
        coeff += gamma1 * pair
    if slot in other_b2b_start:
        coeff += gamma2 * pair
    return coeff


def _z_objective_coeff(
    var_name: str,
    *,
    delta: float,
    triplet_counts: Mapping[tuple[int, int, int], float],
) -> float | None:
    match = _Z_PATTERN.match(var_name)
    if match is None:
        return None

    i, j, k, l = (int(match.group(idx)) for idx in range(1, 5))
    return delta * (
        float(triplet_counts.get((i, j, k), 0.0))
        + float(triplet_counts.get((i, k, l), 0.0))
    )


def _merged_count_map(
    base_values: Any,
    override_values: Any,
    *,
    arity: int,
) -> dict[tuple[int, ...], float]:
    merged = _coerce_count_map(base_values, arity=arity)
    merged.update(_coerce_count_map(override_values, arity=arity))
    return merged


def _coerce_count_map(values: Any, *, arity: int) -> dict[tuple[int, ...], float]:
    if not isinstance(values, Mapping):
        return {}
    coerced: dict[tuple[int, ...], float] = {}
    for raw_key, raw_value in values.items():
        key = _coerce_index_tuple(raw_key, arity=arity)
        if key is None or not isinstance(raw_value, (int, float)):
            continue
        coerced[key] = float(raw_value)
    return coerced


def _coerce_index_tuple(value: Any, *, arity: int) -> tuple[int, ...] | None:
    if not isinstance(value, tuple):
        return None
    if len(value) != arity:
        return None
    if any(not isinstance(item, (int, float)) for item in value):
        return None
    return tuple(int(item) for item in value)


def _slot_set(values: Any) -> set[int]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {int(slot) for slot in values if isinstance(slot, (int, float))}
