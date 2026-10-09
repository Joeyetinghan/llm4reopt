"""Ground-truth evaluator for exam block sequencing with semantic and reference corroboration."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import re
import sys
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gurobipy as gp
from gurobipy import GRB

from framework.core import (
    DeltaRequest,
    GroundTruthArtifact,
    GroundTruthCase,
    GroundTruthCheckResult,
    GroundTruthEvaluator,
    GroundTruthModeResult,
    Patch,
    PatchOp,
    ProblemSpec,
    StructuredEvent,
    apply_patch,
)
from framework.evaluation import infer_feasible
from framework.utils.gurobi_tuning import load_prm_params, lp_artifact_stem
from problems.exam_block_seq._codeedit_heuristic import (
    PROJECTION_STATE_DEAD,
    project_exam_codeedit_heuristic_support,
)
from problems.exam_block_seq.constraint_families import (
    build_frontload_constraint_family,
    build_reserved_virtual_slot_constraint_family,
    build_slot_load_cap_constraint_family,
    canonical_early_slots,
)
from problems.exam_block_seq.dataloader import parse_lp_instance
from problems.exam_block_seq.execution_labels import (
    DIRECT_WARM_START_TOOL,
    HEURISTIC_WARM_START_TOOL,
    TUNED_CONFIG_TOOL,
    normalize_exam_execution_label,
    toolbox_plan_for_exam_execution_label,
)
from problems.exam_block_seq.ground_truth.reference import load_reference_instance
from problems.exam_block_seq.patching import derive_exam_semantic_patches, normalize_exam_patches
from problems.exam_block_seq.paths import GROUND_TRUTH_ROOT, SOLUTIONS_DIR, TUNED_PARAMS_DIR
from problems.exam_block_seq.prompt_params import exam_prompt_params
from problems.exam_block_seq.solution import extract_block_assignments_from_model
from problems.exam_block_seq.structured import build_exam_structured_model
from problems.exam_block_seq.warm_start import apply_exam_warm_start_payload, resolve_exam_warm_start_payload


REFERENCE_CACHE_VERSION = 3
DEFAULT_WARM_START_MODE = "base+heuristic"

_OBJECTIVE_WEIGHT_NAMES = ("alpha", "beta", "gamma1", "gamma2", "delta")
_PAIR_KEYS = ((4, 9), (9, 4))
_REFERENCE_PASS_VERDICTS = {
    "exact_schedule_match",
    "exact_objective_match",
    "interval_consistent",
    "same_infeasibility",
}
_REFERENCE_PARTIAL_VERDICTS = {
    "exact_objective_match",
    "interval_consistent",
    "same_infeasibility",
}
_INFEASIBILITY_STATUSES = {
    int(GRB.INFEASIBLE): "infeasible",
    int(GRB.INF_OR_UNBD): "inf_or_unbd",
    int(GRB.UNBOUNDED): "unbounded",
    "INFEASIBLE": "infeasible",
    "INF_OR_UNBD": "inf_or_unbd",
    "UNBOUNDED": "unbounded",
}


@dataclass(frozen=True)
class ReferenceSolveRequest:
    time_limit: int | float | None
    mip_gap: float | None
    threads: int | None
    execution_label: str
    warm_start_mode: str
    use_tuned_params: bool


class ExamBlockSeqGroundTruthEvaluator(GroundTruthEvaluator):
    def load_cases(self, spec: ProblemSpec) -> list[GroundTruthCase]:
        if spec.ground_truth is None:
            return []
        return list(spec.ground_truth.cases)

    def match_case(
        self,
        spec: ProblemSpec,
        result_payload: dict[str, Any],
        explicit_case_id: str | None = None,
    ) -> GroundTruthCase | None:
        cases = self.load_cases(spec)
        instance_id = _resolve_instance_id(spec, result_payload)
        if explicit_case_id:
            matched = _find_case(cases, explicit_case_id)
            if matched is not None:
                return matched
            if instance_id:
                return _find_case(cases, f"{instance_id}_{explicit_case_id}")
            return None

        payload_case_id = result_payload.get("case_id")
        if isinstance(payload_case_id, str):
            matched = _find_case(cases, payload_case_id)
            if matched is not None:
                return matched

        prompt_id = _resolve_prompt_id(result_payload)
        if instance_id and prompt_id:
            return _find_case(cases, f"{instance_id}_{prompt_id}")
        return None

    def evaluate_case(
        self,
        spec: ProblemSpec,
        case: GroundTruthCase,
        result_payload: dict[str, Any],
        *,
        reference_policy: str = "off",
    ) -> GroundTruthCheckResult:
        result_path = (
            str(result_payload.get("__result_path__"))
            if result_payload.get("__result_path__")
            else None
        )

        semantic_result = _evaluate_semantic_mode(spec, case, result_payload)
        reference_result = _evaluate_reference_mode(
            spec,
            case,
            result_payload,
            reference_policy=reference_policy,
        )

        semantic_verdict = str(semantic_result.checks.get("semantic_verdict") or "unavailable")
        reference_verdict = str(reference_result.checks.get("reference_verdict") or "unresolved")
        overall_verdict = _overall_verdict(semantic_verdict=semantic_verdict, reference_verdict=reference_verdict)
        status = _row_status_from_overall_verdict(overall_verdict)
        matches_ground_truth = _matches_from_overall_verdict(overall_verdict)

        checks: dict[str, Any] = {
            "semantic": semantic_result.matches,
            "reference": reference_result.matches,
            "semantic_verdict": semantic_verdict,
            "reference_verdict": reference_verdict,
            "overall_verdict": overall_verdict,
        }
        checks.update(semantic_result.checks)
        checks.update(reference_result.checks)

        details = {
            "case_description": case.description,
            "semantic": semantic_result.to_dict(),
            "reference": reference_result.to_dict(),
            "overall_verdict": overall_verdict,
        }

        return GroundTruthCheckResult(
            problem_id=spec.metadata.problem_id,
            case_id=case.case_id,
            result_path=result_path,
            status=status,
            matched_case=True,
            matches_ground_truth=matches_ground_truth,
            checks=checks,
            details=details,
            mode_results={
                semantic_result.mode: semantic_result,
                reference_result.mode: reference_result,
            },
        )


def _evaluate_semantic_mode(
    spec: ProblemSpec,
    case: GroundTruthCase,
    result_payload: dict[str, Any],
) -> GroundTruthModeResult:
    prompt_id = str(case.metadata.get("prompt_id") or _resolve_prompt_id(result_payload) or "").strip().upper()
    instance_id = str(case.metadata.get("instance_id") or _resolve_instance_id(spec, result_payload) or "").strip()
    planner_mode = _resolve_planner_mode(result_payload)
    delta_text = _resolve_delta_text(result_payload, case)

    try:
        base_model = _load_exam_structured_model_for_instance(instance_id)
    except Exception as exc:
        return GroundTruthModeResult(
            mode="semantic",
            status="pending",
            matches=None,
            checks={
                "semantic_verdict": "unavailable",
                "semantic_reason": "Could not load the canonical exam instance for semantic checking.",
            },
            details={"error": str(exc), "instance_id": instance_id, "prompt_id": prompt_id},
        )

    expected_model = base_model.copy()
    _apply_expected_prompt_effect(expected_model, prompt_id)
    base_surface = _protected_surface(base_model, prompt_id)
    expected_surface = _protected_surface(expected_model, prompt_id)
    expected_delta = _surface_delta(base_surface, expected_surface)

    candidate_surface: dict[str, Any] | None = None
    details: dict[str, Any] = {
        "instance_id": instance_id,
        "prompt_id": prompt_id,
        "planner_mode": planner_mode,
        "expected_surface_delta": expected_delta,
    }

    if planner_mode == "codeedit":
        annotations = _resolve_planner_annotations(result_payload)
        delta_request = DeltaRequest(
            text=delta_text,
            metadata=_resolve_delta_metadata(result_payload, prompt_id, base_model),
            requested_edit_mode="code",
        )
        projection = project_exam_codeedit_heuristic_support(
            structured=base_model.copy(),
            delta_request=delta_request,
            planner_output=SimpleNamespace(annotations=annotations),
        )
        details["projection"] = projection.as_metadata()

        if projection.projection_state == PROJECTION_STATE_DEAD:
            verdict = "mismatch"
            reason = projection.projection_reason
        elif not projection.projected_patches:
            verdict = "unavailable"
            reason = projection.projection_reason
        else:
            candidate_model = base_model.copy()
            _apply_exam_patches(candidate_model, list(projection.projected_patches), prompt_id, delta_text)
            candidate_surface = _protected_surface(candidate_model, prompt_id)
            verdict = "match" if candidate_surface == expected_surface else "mismatch"
            reason = (
                f"{projection.projection_reason} Compared projected codeedit patches "
                "on the protected exam surface."
            )
    elif _has_patch_semantics_payload(result_payload):
        raw_patches = _extract_chosen_patches(result_payload)
        if not raw_patches:
            verdict = "mismatch"
            reason = "No chosen patches were available for semantic checking."
        else:
            candidate_model = base_model.copy()
            try:
                _apply_exam_patches(candidate_model, raw_patches, prompt_id, delta_text)
            except Exception as exc:
                return GroundTruthModeResult(
                    mode="semantic",
                    status="failed",
                    matches=False,
                    checks={
                        "semantic_verdict": "mismatch",
                        "semantic_reason": "Chosen patches could not be applied to the canonical exam model.",
                    },
                    details={
                        "error": str(exc),
                        "instance_id": instance_id,
                        "prompt_id": prompt_id,
                    },
                )
            candidate_surface = _protected_surface(candidate_model, prompt_id)
            verdict = "match" if candidate_surface == expected_surface else "mismatch"
            reason = "Compared normalized patch semantics on the protected exam surface."
    else:
        verdict = "unavailable"
        reason = "No semantic artifact was available for this run."

    if candidate_surface is not None:
        details["candidate_surface_delta"] = _surface_delta(base_surface, candidate_surface)
        if verdict != "match":
            details["surface_mismatch"] = _surface_delta(expected_surface, candidate_surface)

    status = _mode_status_from_semantic_verdict(verdict)
    matches = _semantic_matches(verdict)
    return GroundTruthModeResult(
        mode="semantic",
        status=status,
        matches=matches,
        checks={
            "semantic_verdict": verdict,
            "semantic_reason": reason,
        },
        details=details,
    )


def _evaluate_reference_mode(
    spec: ProblemSpec,
    case: GroundTruthCase,
    result_payload: dict[str, Any],
    *,
    reference_policy: str,
) -> GroundTruthModeResult:
    artifact = _resolve_reference_artifact(spec, case)
    instance_id = str(case.metadata.get("instance_id") or _resolve_instance_id(spec, result_payload) or "").strip()
    prompt_id = str(case.metadata.get("prompt_id") or _resolve_prompt_id(result_payload) or "").strip()

    if artifact is None:
        return GroundTruthModeResult(
            mode="reference",
            status="pending",
            matches=None,
            checks={
                "reference_available": False,
                "reference_verdict": "unresolved",
                "schedule_match": None,
                "candidate_obj_lb": None,
                "candidate_obj_ub": None,
                "reference_obj_lb": None,
                "reference_obj_ub": None,
            },
            details={"message": "No reference artifact is configured for this case."},
        )

    if not instance_id or not prompt_id:
        return GroundTruthModeResult(
            mode="reference",
            status="pending",
            matches=None,
            checks={
                "reference_available": False,
                "reference_verdict": "unresolved",
                "schedule_match": None,
                "candidate_obj_lb": None,
                "candidate_obj_ub": None,
                "reference_obj_lb": None,
                "reference_obj_ub": None,
            },
            details={"message": "Could not resolve instance_id and prompt_id for reference evaluation."},
        )

    candidate_metrics = _extract_candidate_metrics(result_payload)
    candidate_lb, candidate_ub = _objective_interval(candidate_metrics)

    try:
        reference_payload = _load_or_compute_reference_payload(
            spec,
            artifact,
            case,
            instance_id,
            prompt_id,
            result_payload,
        )
        reference_metrics = dict(reference_payload.get("metrics", {}))
        reference_metrics["schedule"] = _coerce_schedule(reference_metrics.get("schedule"))
        reference_available = True
        reference_error = None
    except Exception as exc:
        reference_payload = {}
        reference_metrics = {}
        reference_available = False
        reference_error = str(exc)

    if not reference_available:
        return GroundTruthModeResult(
            mode="reference",
            status="pending",
            matches=None,
            checks={
                "reference_available": False,
                "reference_verdict": "unresolved",
                "schedule_match": None,
                "candidate_obj_lb": candidate_lb,
                "candidate_obj_ub": candidate_ub,
                "reference_obj_lb": None,
                "reference_obj_ub": None,
            },
            details={
                "message": "Reference evaluation could not be completed.",
                "error": reference_error,
                "reference_policy": reference_policy,
                "candidate_metrics": candidate_metrics,
            },
        )

    objective_tol = float(case.metadata.get("reference_objective_tolerance", 1e-4))
    reference_lb, reference_ub = _objective_interval(reference_metrics)
    schedule_match = _schedule_match(candidate_metrics.get("schedule"), reference_metrics.get("schedule"))
    reference_verdict = _compare_reference_metrics(
        candidate_metrics,
        reference_metrics,
        objective_tol=objective_tol,
    )

    return GroundTruthModeResult(
        mode="reference",
        status=_mode_status_from_reference_verdict(reference_verdict),
        matches=_reference_matches(reference_verdict),
        checks={
            "reference_available": True,
            "reference_verdict": reference_verdict,
            "schedule_match": schedule_match,
            "candidate_obj_lb": candidate_lb,
            "candidate_obj_ub": candidate_ub,
            "reference_obj_lb": reference_lb,
            "reference_obj_ub": reference_ub,
        },
        details={
            "reference_instance_id": instance_id,
            "reference_prompt_id": prompt_id,
            "reference_script": reference_payload.get("reference_script"),
            "reference_cache_path": reference_payload.get("cache_path"),
            "reference_request": reference_payload.get("reference_request"),
            "reference_changes": reference_payload.get("reference_changes", []),
            "assumptions": reference_payload.get("assumptions", []),
            "prompt_task": reference_payload.get("prompt_task"),
            "script_output": reference_payload.get("script_output", ""),
            "candidate_metrics": candidate_metrics,
            "reference_metrics": reference_metrics,
        },
    )


def _overall_verdict(*, semantic_verdict: str, reference_verdict: str) -> str:
    if semantic_verdict == "mismatch" or reference_verdict == "contradicted":
        return "fail"
    if semantic_verdict == "match" and reference_verdict in _REFERENCE_PASS_VERDICTS:
        return "pass"
    if semantic_verdict == "unavailable" and reference_verdict == "exact_schedule_match":
        return "behavioral_pass"
    if semantic_verdict == "match" and reference_verdict == "unresolved":
        return "partial"
    if semantic_verdict == "unavailable" and reference_verdict in _REFERENCE_PARTIAL_VERDICTS:
        return "partial"
    return "unresolved"


def _row_status_from_overall_verdict(verdict: str) -> str:
    if verdict in {"pass", "behavioral_pass"}:
        return "passed"
    if verdict == "fail":
        return "failed"
    if verdict == "partial":
        return "partial"
    return "pending"


def _matches_from_overall_verdict(verdict: str) -> bool | None:
    if verdict in {"pass", "behavioral_pass"}:
        return True
    if verdict == "fail":
        return False
    return None


def _mode_status_from_semantic_verdict(verdict: str) -> str:
    if verdict == "match":
        return "passed"
    if verdict == "mismatch":
        return "failed"
    return "pending"


def _mode_status_from_reference_verdict(verdict: str) -> str:
    if verdict in _REFERENCE_PASS_VERDICTS:
        return "passed"
    if verdict == "contradicted":
        return "failed"
    return "pending"


def _semantic_matches(verdict: str) -> bool | None:
    if verdict == "match":
        return True
    if verdict == "mismatch":
        return False
    return None


def _reference_matches(verdict: str) -> bool | None:
    if verdict in _REFERENCE_PASS_VERDICTS:
        return True
    if verdict == "contradicted":
        return False
    return None


@lru_cache(maxsize=None)
def _cached_exam_structured_model_for_instance(instance_id: str):
    model, state = load_reference_instance(instance_id)
    try:
        state = dict(state)
        state["instance_id"] = instance_id
        instance_dir = _coerce_instance_dir(state)
        lp_path = instance_dir / "model.lp" if instance_dir is not None else None
        if lp_path is None:
            raise FileNotFoundError(f"Could not resolve LP path for exam instance {instance_id}")
        return _build_structured_model_from_reference_state(state, lp_path)
    finally:
        if hasattr(model, "dispose"):
            model.dispose()


def _load_exam_structured_model_for_instance(instance_id: str):
    # Semantic evaluation reuses the same canonical instance across many runs.
    return _cached_exam_structured_model_for_instance(instance_id).copy()


def _apply_expected_prompt_effect(model, prompt_id: str) -> None:
    prompt_key = str(prompt_id).strip().upper()
    if prompt_key == "P6":
        for child in ("P4", "P2", "P1"):
            _apply_expected_prompt_effect(model, child)
        return

    if prompt_key == "P1":
        _apply_exam_patches(
            model,
            [
                Patch(
                    op=PatchOp.UPDATE_PARAMETER,
                    target={"name": "reserved_slots"},
                    update={"name": "reserved_slots", "value": [_penultimate_evening_slot(model)]},
                )
            ],
            prompt_key,
            prompt_key,
        )
        return

    if prompt_key == "P2":
        patches: list[Patch] = []
        for pair in _PAIR_KEYS:
            patches.append(
                Patch(
                    op=PatchOp.UPDATE_PARAMETER,
                    target={"name": "pair_counts"},
                    scope={"entity": list(pair)},
                    update={
                        "name": "p",
                        "key": list(pair),
                        "value": _effective_pair_count(model, pair) + 120.0,
                    },
                )
            )
        _apply_exam_patches(model, patches, prompt_key, prompt_key)
        return

    if prompt_key == "P3":
        prompt_params = exam_prompt_params(prompt_key, context=model.parameters)
        cutoff_exclusive = int(prompt_params["slot_cutoff_exclusive"])
        early_slots = canonical_early_slots(model.parameters.get("blocks", []), cutoff_exclusive)
        family = build_frontload_constraint_family(
            large_blocks=model.parameters.get("large_blocks", []),
            early_slots=early_slots,
        )
        _apply_exam_patches(
            model,
            [
                Patch(
                    op=PatchOp.UPDATE_PARAMETER,
                    target={"name": "early_slots"},
                    update={"name": "early_slots", "value": early_slots},
                ),
                Patch(
                    op=PatchOp.UPDATE_CONSTRAINT_LHS,
                    target={"constraint": "frontload"},
                    update={"lhs_spec": family.lhs_spec},
                ),
            ],
            prompt_key,
            prompt_key,
        )
        return

    if prompt_key == "P4":
        gamma2_weight = float(model.objectives["gamma2"].weight)
        gamma2_base = float((model.extras.get("base_parameters") or {}).get("gamma2", gamma2_weight))
        _apply_exam_patches(
            model,
            [
                Patch(
                    op=PatchOp.UPDATE_OBJECTIVE_WEIGHT,
                    target={"objective": "gamma1"},
                    update={"weight": gamma2_weight},
                ),
                Patch(
                    op=PatchOp.UPDATE_OBJECTIVE_WEIGHT,
                    target={"objective": "beta"},
                    update={"weight": 20.0 * gamma2_base},
                ),
            ],
            prompt_key,
            prompt_key,
        )
        return

    if prompt_key == "P5":
        family = build_slot_load_cap_constraint_family(
            row_name="day_2",
            slots=_slots_for_day(2, model) or [4, 5, 6],
            real_blocks=[
                int(block)
                for block in model.parameters.get("blocks", [])
                if int(block) not in {int(v) for v in model.parameters.get("virtual_blocks", [])}
            ],
            block_enrollment=dict(model.parameters.get("block_enrollment") or {}),
            cap=4000.0,
        )
        _apply_exam_patches(
            model,
            [
                Patch(
                    op=PatchOp.ADD_CONSTRAINT_FAMILY,
                    target={"constraint": family.name},
                    update={"constraint": family},
                )
            ],
            prompt_key,
            prompt_key,
        )


def _apply_exam_patches(model, raw_patches: list[Any], prompt_id: str, delta_text: str) -> None:
    event = StructuredEvent(
        raw_text=str(delta_text or ""),
        annotations={"delta_metadata": {"prompt_id": prompt_id}},
    )
    working = model
    for raw_patch in raw_patches:
        patch = _coerce_patch(raw_patch)
        normalized = normalize_exam_patches([patch], working, event) or [patch]
        for item in normalized:
            apply_patch(working, item, in_place=True)
            for derived in derive_exam_semantic_patches(item, working):
                apply_patch(working, derived, in_place=True)


def _coerce_patch(raw_patch: Any) -> Patch:
    if isinstance(raw_patch, Patch):
        return raw_patch
    if not isinstance(raw_patch, dict):
        raise TypeError(f"Unsupported patch payload: {type(raw_patch)!r}")
    return Patch(
        op=PatchOp(str(raw_patch.get("op"))),
        target=dict(raw_patch.get("target") or {}),
        scope=dict(raw_patch.get("scope") or {}),
        update=dict(raw_patch.get("update") or {}),
        notes=str(raw_patch.get("notes") or ""),
    )


def _protected_surface(model, prompt_id: str) -> dict[str, Any]:
    prompt_key = str(prompt_id).strip().upper()
    if prompt_key == "P1":
        # The executable formulation is the reservation constraint; reserved_slots
        # is only an input surface used to derive that family. Any virtual block
        # may reserve the grounded slot.
        return {
            "reserved_virtual_slot": _reserved_virtual_slot_surface(model),
        }
    if prompt_key == "P2":
        return {"effective_pair_counts": _pair_count_surface(model)}
    if prompt_key == "P3":
        return {
            "early_slots": sorted(int(slot) for slot in model.parameters.get("early_slots", [])),
            "frontload": _constraint_family_surface(model.constraints.get("frontload")),
        }
    if prompt_key == "P4":
        return {"objective_weights": _objective_weight_surface(model)}
    if prompt_key == "P5":
        return {"slot_load_cap": _constraint_family_surface(model.constraints.get("slot_load_cap"))}
    if prompt_key == "P6":
        return {
            **_protected_surface(model, "P4"),
            **_protected_surface(model, "P2"),
            **_protected_surface(model, "P1"),
        }
    return {}


def _reserved_virtual_slot_surface(model) -> dict[str, Any]:
    surface = _constraint_family_surface(model.constraints.get("reserved_virtual_slot"))
    virtual_blocks = {int(block) for block in model.parameters.get("virtual_blocks", [])}
    rows_by_slot: dict[str, Any] = {}
    for row_key, row_spec in surface["rows"].items():
        slots = [int(slot) for slot in row_spec.get("slots", [])]
        slot_key = ",".join(str(slot) for slot in slots)
        row_payload: dict[str, Any] = {
            "slots": slots,
            "rhs": surface["rhs"].get(row_key),
        }
        fixed_block = row_spec.get("fixed_block")
        if fixed_block is not None:
            block = int(fixed_block)
            row_payload["fixed_block"] = "virtual" if block in virtual_blocks else f"non_virtual:{block}"
        rows_by_slot[slot_key] = row_payload
    return {
        "sense": surface["sense"],
        "rows": rows_by_slot,
    }


def _constraint_family_surface(family) -> dict[str, Any]:
    if family is None:
        return {"rows": {}, "rhs": {}, "sense": None}
    lhs_spec = family.lhs_spec if isinstance(family.lhs_spec, dict) else {}
    rows = dict(lhs_spec.get("rows") or {})
    normalized_rows: dict[str, Any] = {}
    for row_key, row_spec in rows.items():
        normalized_row: dict[str, Any] = {}
        if isinstance(row_spec, dict):
            if row_spec.get("fixed_block") is not None:
                normalized_row["fixed_block"] = int(row_spec["fixed_block"])
            if row_spec.get("slots") is not None:
                normalized_row["slots"] = sorted(int(slot) for slot in row_spec.get("slots", []))
            if row_spec.get("block_weights") is not None:
                normalized_row["block_weights"] = {
                    str(int(block)): float(weight)
                    for block, weight in sorted(dict(row_spec.get("block_weights") or {}).items(), key=lambda item: int(item[0]))
                }
        normalized_rows[_normalize_key(row_key)] = normalized_row

    rhs_spec = family.rhs_spec if isinstance(family.rhs_spec, dict) else {}
    normalized_rhs = {
        _normalize_key(row_key): float(value)
        for row_key, value in sorted(rhs_spec.items(), key=lambda item: str(item[0]))
    }
    return {
        "sense": family.sense,
        "rows": normalized_rows,
        "rhs": normalized_rhs,
    }


def _pair_count_surface(model) -> dict[str, float]:
    raw_keys = set(dict(model.parameters.get("pair_counts") or {}).keys()) | set(dict(model.parameters.get("p") or {}).keys())
    keys = {_coerce_pair_key(key) for key in raw_keys}
    keys.discard(None)
    keys |= set(_PAIR_KEYS)
    return {
        f"{pair[0]},{pair[1]}": float(_effective_pair_count(model, pair))
        for pair in sorted(keys)
    }


def _coerce_pair_key(key: Any) -> tuple[int, int] | None:
    if isinstance(key, (tuple, list)) and len(key) >= 2:
        try:
            return (int(key[0]), int(key[1]))
        except (TypeError, ValueError):
            parts = re.findall(r"-?\d+", ",".join(str(part) for part in key))
            if len(parts) >= 2:
                return (int(parts[0]), int(parts[1]))
    if isinstance(key, str):
        parts = re.findall(r"-?\d+", key)
        if len(parts) >= 2:
            return (int(parts[0]), int(parts[1]))
    return None


def _objective_weight_surface(model) -> dict[str, float]:
    return {
        name: float(model.objectives.get(name).weight if model.objectives.get(name) is not None else model.parameters.get(name, 0.0))
        for name in _OBJECTIVE_WEIGHT_NAMES
    }


def _surface_delta(base: Any, updated: Any) -> Any:
    if isinstance(base, dict) and isinstance(updated, dict):
        payload: dict[str, Any] = {}
        keys = set(base.keys()) | set(updated.keys())
        for key in keys:
            diff = _surface_delta(base.get(key), updated.get(key))
            if diff is not None:
                payload[str(key)] = diff
        return payload or None
    if isinstance(base, list) and isinstance(updated, list):
        return None if base == updated else {"from": base, "to": updated}
    if _same_number(base, updated):
        return None
    if base == updated:
        return None
    return {"from": base, "to": updated}


def _load_or_compute_reference_payload(
    spec: ProblemSpec,
    artifact: GroundTruthArtifact,
    case: GroundTruthCase,
    instance_id: str,
    prompt_id: str,
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    request = _resolve_reference_request(spec, result_payload)
    payload = _load_precomputed_reference_payload(
        spec,
        instance_id,
        prompt_id,
        request=request,
    )
    if payload is None:
        cache_path = _reference_cache_path(spec, case.case_id)
        if cache_path.exists():
            with cache_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if _reference_payload_matches_request(payload, request=request):
                payload["cache_path"] = str(cache_path)
                return payload

        reference_script = _resolve_reference_script(artifact.path, instance_id, prompt_id)
        payload = _compute_reference_payload(
            reference_script,
            spec,
            expected_instance_id=instance_id,
            expected_prompt_id=prompt_id,
            result_payload=result_payload,
            request=request,
        )
        payload["reference_script"] = str(reference_script)

    cache_path = _reference_cache_path(spec, case.case_id)
    payload["reference_cache_version"] = REFERENCE_CACHE_VERSION
    payload["cache_path"] = str(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return payload


def _reference_cache_path(spec: ProblemSpec, case_id: str) -> Path:
    return spec.metadata.package_root / "ground_truth" / "cache" / f"{case_id}.json"


def _resolve_reference_script(artifact_path: Path, instance_id: str, prompt_id: str) -> Path:
    # One module serves every case: its build_reference(instance_id, prompt_id) applies the gold edit.
    if artifact_path.is_file():
        return artifact_path.resolve()
    raise FileNotFoundError(
        f"Reference module not found for instance {instance_id} prompt {prompt_id}: {artifact_path}"
    )


def _compute_reference_payload(
    reference_script: Path,
    spec: ProblemSpec,
    *,
    expected_instance_id: str,
    expected_prompt_id: str,
    result_payload: dict[str, Any],
    request: ReferenceSolveRequest,
) -> dict[str, Any]:
    module_name = f"_exam_ground_truth_{reference_script.stem}_{uuid.uuid4().hex}"
    spec_obj = importlib.util.spec_from_file_location(module_name, reference_script)
    if spec_obj is None or spec_obj.loader is None:
        raise RuntimeError(f"Unable to import reference script: {reference_script}")

    module = importlib.util.module_from_spec(spec_obj)
    captured = io.StringIO()
    repo_root = spec.metadata.package_root.parents[1]
    added_repo_root = False
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
        added_repo_root = True
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            spec_obj.loader.exec_module(module)
            module = module.build_reference(expected_instance_id, expected_prompt_id)
    finally:
        if added_repo_root:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(repo_root))

    model = getattr(module, "m", None)
    if model is None or not isinstance(model, gp.Model):
        raise RuntimeError(f"Reference script did not expose a gurobipy model named 'm': {reference_script}")

    script_instance_id = getattr(module, "INSTANCE_ID", None)
    if script_instance_id and str(script_instance_id) != expected_instance_id:
        raise RuntimeError(
            f"Reference script {reference_script} is pinned to {script_instance_id}, not {expected_instance_id}"
        )

    script_prompt_id = getattr(module, "PROMPT_ID", None)
    if script_prompt_id and str(script_prompt_id) != expected_prompt_id:
        raise RuntimeError(
            f"Reference script {reference_script} is pinned to {script_prompt_id}, not {expected_prompt_id}"
        )

    state = getattr(module, "state", None)
    instance_dir = _coerce_instance_dir(state)
    lp_path = instance_dir / "model.lp" if instance_dir is not None else None
    tuned_param_path = _resolve_tuned_param_path(lp_path, TUNED_PARAMS_DIR) if request.use_tuned_params and lp_path else None
    warm_start_payload, resolved_warm_start_mode = _resolve_reference_warm_start_payload(
        state,
        lp_path,
        warm_start_mode=request.warm_start_mode,
    )

    model.Params.OutputFlag = 0
    if tuned_param_path is not None:
        for name, value in load_prm_params(tuned_param_path).items():
            if value is None:
                continue
            model.setParam(str(name), value)
    if request.time_limit is not None:
        model.Params.TimeLimit = float(request.time_limit)
    if request.mip_gap is not None:
        model.Params.MIPGap = float(request.mip_gap)
    if request.threads is not None:
        model.Params.Threads = int(request.threads)
    apply_exam_warm_start_payload(model, warm_start_payload)

    model.optimize()
    if model.Status == GRB.INF_OR_UNBD:
        model.Params.Presolve = 0
        model.optimize()

    has_incumbent = int(getattr(model, "SolCount", 0) or 0) > 0
    objective = float(model.ObjVal) if has_incumbent else None
    obj_bound = _safe_model_float(model, "ObjBound")
    mip_gap = _safe_model_float(model, "MIPGap")
    schedule = None
    if has_incumbent and isinstance(state, dict):
        schedule = extract_block_assignments_from_model(
            model,
            [int(block) for block in state.get("blocks", [])],
        )

    payload = {
        "reference_cache_version": REFERENCE_CACHE_VERSION,
        "reference_source": "live_evaluator",
        "reference_request": {
            "time_limit": request.time_limit,
            "mip_gap": request.mip_gap,
            "threads": request.threads,
            "execution_label": request.execution_label,
            "warm_start_mode": request.warm_start_mode,
            "use_tuned_params": request.use_tuned_params,
        },
        "metrics": {
            "status": int(model.Status),
            "feasible": infer_feasible(int(model.Status), objective),
            "objective": objective,
            "obj_bound": obj_bound,
            "sol_count": int(getattr(model, "SolCount", 0) or 0),
            "runtime": float(getattr(model, "Runtime", 0.0) or 0.0),
            "time_limit": request.time_limit,
            "mip_gap": mip_gap,
            "threads": request.threads,
            "execution_label": request.execution_label,
            "warm_start_mode": resolved_warm_start_mode,
            "use_tuned_params": bool(tuned_param_path is not None),
            "tuned_param_path": str(tuned_param_path) if tuned_param_path is not None else "",
            "schedule": schedule or {},
        },
        "reference_changes": list(getattr(module, "REFERENCE_CHANGES", [])),
        "assumptions": list(getattr(module, "ASSUMPTIONS", [])),
        "prompt_task": getattr(module, "PROMPT_TASK", ""),
        "script_output": captured.getvalue(),
    }
    if hasattr(model, "dispose"):
        model.dispose()
    return payload


def _resolve_reference_request(spec: ProblemSpec, result_payload: dict[str, Any]) -> ReferenceSolveRequest:
    execution_label = _resolve_execution_label(result_payload)
    return ReferenceSolveRequest(
        time_limit=_resolve_time_limit(spec, result_payload),
        mip_gap=_resolve_mip_gap(spec, result_payload),
        threads=_resolve_threads(spec, result_payload),
        execution_label=execution_label,
        warm_start_mode=_resolve_warm_start_mode(result_payload, execution_label=execution_label),
        use_tuned_params=_resolve_use_tuned_params(result_payload, execution_label=execution_label),
    )


def _resolve_instance_id(spec: ProblemSpec, result_payload: dict[str, Any]) -> str:
    step = _result_step_payload(result_payload)
    candidates = [
        result_payload.get("instance"),
        result_payload.get("instance_id"),
        (result_payload.get("input") or {}).get("config", {}).get("instance_id"),
        (result_payload.get("input") or {}).get("config", {}).get("instance"),
        step.get("solve_meta", {}).get("instance_id") if step else None,
        result_payload.get("base_solve_meta", {}).get("instance_id"),
        result_payload.get("new_solve_meta", {}).get("instance_id"),
        spec.config_metadata.get("config", {}).get("instance_id"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    input_config = (result_payload.get("input") or {}).get("config", {})
    instance_dir = input_config.get("instance_dir")
    if instance_dir:
        return _instance_id_from_path(instance_dir)
    return ""


def _resolve_prompt_id(result_payload: dict[str, Any]) -> str:
    candidates = [
        result_payload.get("prompt_id"),
        result_payload.get("prompt"),
        result_payload.get("case_id"),
        (result_payload.get("input") or {}).get("prompt_id"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            match = re.search(r"(P\d+)$", candidate)
            if match:
                return match.group(1)
    result_name = str(result_payload.get("__result_name__", ""))
    match = re.search(r"_(P\d+)(?:_|\.|$)", result_name)
    return match.group(1) if match else ""


def _resolve_time_limit(spec: ProblemSpec, result_payload: dict[str, Any]) -> int | float | None:
    step = _result_step_payload(result_payload)
    candidates = [
        result_payload.get("time_limit"),
        (result_payload.get("input") or {}).get("config", {}).get("time_limit"),
        step.get("solve_meta", {}).get("time_limit") if step else None,
        result_payload.get("base_solve_meta", {}).get("time_limit"),
        result_payload.get("new_solve_meta", {}).get("time_limit"),
        spec.config_metadata.get("config", {}).get("time_limit"),
    ]
    for candidate in candidates:
        if isinstance(candidate, (int, float)):
            return candidate
    return None


def _resolve_mip_gap(spec: ProblemSpec, result_payload: dict[str, Any]) -> float | None:
    step = _result_step_payload(result_payload)
    candidates = [
        result_payload.get("mip_gap"),
        (result_payload.get("input") or {}).get("config", {}).get("mip_gap"),
        step.get("solve_meta", {}).get("mip_gap") if step else None,
        result_payload.get("base_solve_meta", {}).get("mip_gap"),
        result_payload.get("new_solve_meta", {}).get("mip_gap"),
        spec.config_metadata.get("config", {}).get("mip_gap"),
    ]
    for candidate in candidates:
        if isinstance(candidate, (int, float)):
            return float(candidate)
    return None


def _resolve_threads(spec: ProblemSpec, result_payload: dict[str, Any]) -> int | None:
    step = _result_step_payload(result_payload)
    candidates = [
        result_payload.get("threads"),
        (result_payload.get("input") or {}).get("config", {}).get("threads"),
        step.get("solve_meta", {}).get("threads") if step else None,
        result_payload.get("base_solve_meta", {}).get("threads"),
        result_payload.get("new_solve_meta", {}).get("threads"),
        spec.config_metadata.get("config", {}).get("threads"),
    ]
    for candidate in candidates:
        if isinstance(candidate, (int, float)):
            return int(candidate)
    return None


def _resolve_execution_label(result_payload: dict[str, Any]) -> str:
    step = _result_step_payload(result_payload)
    candidates = [
        result_payload.get("execution_label"),
        step.get("strategy_selection", {}).get("execution_label") if step else None,
        (result_payload.get("input") or {}).get("strategy"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            return normalize_exam_execution_label(candidate)
        except ValueError:
            return candidate.strip()
    return ""


def _resolve_warm_start_mode(result_payload: dict[str, Any], *, execution_label: str) -> str:
    step = _result_step_payload(result_payload)
    candidates = [
        step.get("solve_meta", {}).get("warm_start_mode") if step else None,
        result_payload.get("new_solve_meta", {}).get("warm_start_mode"),
        result_payload.get("solve_meta", {}).get("warm_start_mode"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return str(candidate).strip()
    try:
        toolbox = set(toolbox_plan_for_exam_execution_label(execution_label))
    except Exception:
        toolbox = set()
    if DIRECT_WARM_START_TOOL in toolbox and HEURISTIC_WARM_START_TOOL in toolbox:
        return "base+heuristic"
    if DIRECT_WARM_START_TOOL in toolbox:
        return "base"
    if HEURISTIC_WARM_START_TOOL in toolbox:
        return "heuristic"
    return "none"


def _resolve_use_tuned_params(result_payload: dict[str, Any], *, execution_label: str) -> bool:
    try:
        toolbox = set(toolbox_plan_for_exam_execution_label(execution_label))
    except Exception:
        toolbox = set()
    if TUNED_CONFIG_TOOL in toolbox:
        return True
    step = _result_step_payload(result_payload)
    strategy = str((step or {}).get("strategy") or result_payload.get("strategy") or "").strip().lower()
    return "tuned" in strategy


def _extract_candidate_metrics(result_payload: dict[str, Any]) -> dict[str, Any]:
    step = _result_step_payload(result_payload)
    step_solve_meta = dict(step.get("solve_meta") or {}) if step else {}
    status = _first_solver_status(
        step_solve_meta.get("status"),
        result_payload.get("new_solve_meta", {}).get("status"),
        result_payload.get("solve_meta", {}).get("status"),
        result_payload.get("status"),
    )
    objective = _first_finite_number(
        step.get("objective") if step else None,
        step_solve_meta.get("objective"),
        result_payload.get("new_cost"),
        result_payload.get("new_obj"),
        result_payload.get("objective"),
        result_payload.get("solve_meta", {}).get("objective"),
    )
    obj_bound = _first_finite_number(
        step_solve_meta.get("obj_bound"),
        result_payload.get("obj_bound"),
        result_payload.get("new_solve_meta", {}).get("obj_bound"),
        result_payload.get("solve_meta", {}).get("obj_bound"),
    )
    sol_count = _first_number(
        step_solve_meta.get("sol_count"),
        result_payload.get("new_solve_meta", {}).get("sol_count"),
        result_payload.get("solve_meta", {}).get("sol_count"),
        result_payload.get("sol_count"),
    )
    runtime = _first_number(
        step_solve_meta.get("runtime"),
        result_payload.get("runtime"),
        result_payload.get("solve_meta", {}).get("runtime"),
        result_payload.get("new_solve_meta", {}).get("runtime"),
    )
    feasible = result_payload.get("feasible")
    if feasible is None:
        feasible = infer_feasible(status, objective)
        if feasible is None and sol_count is not None:
            feasible = int(sol_count) > 0

    return {
        "status": status,
        "feasible": None if feasible is None else bool(feasible),
        "objective": objective,
        "obj_bound": obj_bound,
        "sol_count": int(sol_count) if sol_count is not None else None,
        "runtime": runtime,
        "schedule": _extract_candidate_schedule(result_payload),
        "execution_label": _resolve_execution_label(result_payload),
        "warm_start_mode": _resolve_warm_start_mode(result_payload, execution_label=_resolve_execution_label(result_payload)),
        "use_tuned_params": _resolve_use_tuned_params(result_payload, execution_label=_resolve_execution_label(result_payload)),
    }


def _objective_interval(metrics: dict[str, Any]) -> tuple[float | None, float | None]:
    objective = _first_finite_number(metrics.get("objective"))
    obj_bound = _first_finite_number(metrics.get("obj_bound"))
    if objective is None:
        return obj_bound, None
    if obj_bound is None:
        obj_bound = objective if _is_optimal(metrics.get("status")) else None
    if obj_bound is not None and obj_bound > objective and _is_optimal(metrics.get("status")):
        obj_bound = objective
    return obj_bound, objective


def _compare_reference_metrics(
    candidate_metrics: dict[str, Any],
    reference_metrics: dict[str, Any],
    *,
    objective_tol: float,
) -> str:
    schedule_match = _schedule_match(candidate_metrics.get("schedule"), reference_metrics.get("schedule"))
    if schedule_match is True:
        return "exact_schedule_match"

    candidate_status = candidate_metrics.get("status")
    reference_status = reference_metrics.get("status")
    candidate_objective = _first_finite_number(candidate_metrics.get("objective"))
    reference_objective = _first_finite_number(reference_metrics.get("objective"))
    if _is_optimal(candidate_status) and _is_optimal(reference_status):
        if candidate_objective is not None and reference_objective is not None:
            if abs(candidate_objective - reference_objective) <= objective_tol:
                return "exact_objective_match"

    candidate_lb, candidate_ub = _objective_interval(candidate_metrics)
    reference_lb, reference_ub = _objective_interval(reference_metrics)
    candidate_infeasible = _infeasibility_label(candidate_metrics)
    reference_infeasible = _infeasibility_label(reference_metrics)

    if candidate_infeasible and reference_infeasible and candidate_infeasible == reference_infeasible:
        if candidate_ub is None and reference_ub is None:
            return "same_infeasibility"

    if candidate_ub is not None and reference_ub is not None:
        if (
            candidate_lb is not None
            and reference_lb is not None
            and max(candidate_lb, reference_lb) <= min(candidate_ub, reference_ub) + objective_tol
        ):
            return "interval_consistent"
        return "contradicted"

    if candidate_ub is not None and reference_infeasible:
        return "contradicted"
    if reference_ub is not None and candidate_infeasible:
        return "contradicted"

    if candidate_ub is not None and reference_lb is not None and reference_lb > candidate_ub + objective_tol:
        return "contradicted"
    if reference_ub is not None and candidate_lb is not None and candidate_lb > reference_ub + objective_tol:
        return "contradicted"

    return "unresolved"


def _reference_payload_matches_request(
    payload: dict[str, Any],
    *,
    request: ReferenceSolveRequest,
) -> bool:
    if int(payload.get("reference_cache_version", 0) or 0) != REFERENCE_CACHE_VERSION:
        return False
    metrics = dict(payload.get("metrics", {}))
    return all(
        (
            _same_number_or_none(metrics.get("time_limit"), request.time_limit),
            _same_number_or_none(metrics.get("mip_gap"), request.mip_gap),
            _same_number_or_none(metrics.get("threads"), request.threads),
            _same_text_or_none(metrics.get("execution_label"), request.execution_label),
            _same_text_or_none(metrics.get("warm_start_mode"), request.warm_start_mode),
            _same_bool_or_none(metrics.get("use_tuned_params"), request.use_tuned_params),
        )
    )


def _load_precomputed_reference_payload(
    spec: ProblemSpec,
    instance_id: str,
    prompt_id: str,
    *,
    request: ReferenceSolveRequest,
) -> dict[str, Any] | None:
    for summary_path in _precomputed_reference_summary_candidates(
        spec,
        instance_id=instance_id,
        prompt_id=prompt_id,
        time_limit=request.time_limit,
    ):
        if not summary_path.exists():
            continue
        row = _load_summary_row(summary_path)
        if row is None:
            continue
        payload = _precomputed_summary_to_reference_payload(row, summary_path)
        if _reference_payload_matches_request(payload, request=request):
            return payload
    return None


def _precomputed_reference_summary_candidates(
    spec: ProblemSpec,
    *,
    instance_id: str,
    prompt_id: str,
    time_limit: int | float | None,
) -> list[Path]:
    del spec
    roots: list[Path] = []
    if time_limit is not None:
        roots.append(GROUND_TRUTH_ROOT / _format_time_limit_tag(time_limit))
    elif GROUND_TRUTH_ROOT.exists():
        roots.extend(path for path in sorted(GROUND_TRUTH_ROOT.iterdir()) if path.is_dir())
    unique_roots: list[Path] = []
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved not in unique_roots:
            unique_roots.append(resolved)
    return [root / instance_id / prompt_id / "summary.json" for root in unique_roots]


def _load_summary_row(summary_path: Path) -> dict[str, Any] | None:
    with summary_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                return row
    return None


def _precomputed_summary_to_reference_payload(row: dict[str, Any], summary_path: Path) -> dict[str, Any]:
    status = row.get("status_code", row.get("status"))
    objective = _first_finite_number(row.get("objective"))
    obj_bound = _first_finite_number(row.get("obj_bound"))
    sol_count = _first_number(row.get("sol_count"))
    feasible = infer_feasible(status, objective)
    if feasible is None and sol_count is not None:
        feasible = int(sol_count) > 0
    schedule = _coerce_schedule(
        row.get("schedule")
        or row.get("normalized_schedule")
        or row.get("schedule_json")
    )
    use_tuned_params = _coerce_bool(row.get("use_tuned_params"))
    tuned_param_path = str(row.get("tuned_param_path", "") or "")
    if use_tuned_params is None:
        use_tuned_params = bool(tuned_param_path)
    execution_label = str(row.get("execution_label", "") or "").strip()
    return {
        "reference_cache_version": REFERENCE_CACHE_VERSION,
        "reference_source": "precomputed_summary",
        "reference_result_path": str(summary_path),
        "reference_script": row.get("reference_script", ""),
        "reference_request": {
            "time_limit": _first_number(row.get("time_limit_s")),
            "mip_gap": _first_number(row.get("mip_gap_limit")),
            "threads": _first_number(row.get("threads")),
            "execution_label": execution_label,
            "warm_start_mode": str(row.get("warm_start_mode", "") or ""),
            "use_tuned_params": bool(use_tuned_params),
        },
        "metrics": {
            "status": status,
            "feasible": None if feasible is None else bool(feasible),
            "objective": objective,
            "obj_bound": obj_bound,
            "sol_count": int(sol_count) if sol_count is not None else None,
            "runtime": _first_number(row.get("runtime_attr_s"), row.get("wall_time_s")),
            "time_limit": _first_number(row.get("time_limit_s")),
            "mip_gap": _first_number(row.get("mip_gap_limit")),
            "threads": _first_number(row.get("threads")),
            "execution_label": execution_label,
            "warm_start_mode": str(row.get("warm_start_mode", "") or ""),
            "use_tuned_params": bool(use_tuned_params),
            "tuned_param_path": tuned_param_path,
            "schedule": schedule or {},
        },
        "reference_changes": _coerce_json_list(row.get("modification_details")),
        "assumptions": _coerce_json_list(row.get("assumptions")),
        "prompt_task": row.get("prompt_task", ""),
        "script_output": row.get("script_output", ""),
    }


def _resolve_reference_warm_start_payload(
    state: Any,
    lp_path: Path | None,
    *,
    warm_start_mode: str,
) -> tuple[dict[str, Any] | dict[Any, Any] | None, str]:
    if not isinstance(state, dict) or lp_path is None or warm_start_mode == "none":
        return None, "none"

    structured = _build_structured_model_from_reference_state(state, lp_path)
    structured.parameters["disable_default_warm_start"] = warm_start_mode not in {"heuristic", "base+heuristic"}
    if warm_start_mode in {"base", "base+heuristic"}:
        warm_start_path = _resolve_saved_solution_path(lp_path, SOLUTIONS_DIR)
        if warm_start_path is not None:
            structured.parameters["warm_start"] = {"sol_path": str(warm_start_path)}
    warm_start_payload, resolved_mode = resolve_exam_warm_start_payload(structured)
    return warm_start_payload, resolved_mode


def _build_structured_model_from_reference_state(state: dict[str, Any], lp_path: Path):
    structured = build_exam_structured_model(
        lp_path=str(lp_path),
        blocks=[int(block) for block in state.get("blocks", [])],
        slots_per_day=int(state.get("slots_per_day", 3) or 3),
        slot_times=[str(label) for label in state.get("slot_times", [])],
        triple_24_start=sorted(int(slot) for slot in state.get("triple_24_start", [])),
        triple_day_start=sorted(int(slot) for slot in state.get("triple_day_start", [])),
        eve_morn_start=sorted(int(slot) for slot in state.get("eve_morn_start", [])),
        other_b2b_start=sorted(int(slot) for slot in state.get("other_b2b_start", [])),
        weights=dict(state.get("weights") or {}),
        reserved_slots=[int(slot) for slot in state.get("reserved_slots", [])],
        real_blocks=len(state.get("real_blocks", [])),
        virtual_blocks=[int(block) for block in state.get("virtual_blocks", [])],
        large_blocks=[int(block) for block in state.get("large_blocks", [])],
        early_slots=[int(slot) for slot in state.get("early_slots", [])],
        block_enrollment={
            int(key): float(value) for key, value in dict(state.get("block_enrollment") or {}).items()
        },
        pair_counts={tuple(map(int, key)): float(value) for key, value in dict(state.get("pair_counts") or {}).items()},
        triplet_counts={
            tuple(map(int, key)): float(value) for key, value in dict(state.get("triplet_counts") or {}).items()
        },
        instance_id=str(state.get("instance_id", "") or ""),
    )
    for family_name, family in dict(state.get("_semantic_families") or {}).items():
        structured.constraints[str(family_name)] = family.copy()
    return structured


def _coerce_instance_dir(state: Any) -> Path | None:
    if not isinstance(state, dict):
        return None
    raw = state.get("instance_dir")
    if raw in {None, ""}:
        return None
    return Path(raw).expanduser().resolve()


def _resolve_saved_solution_path(lp_path: Path | None, base_solution_dir: str | Path) -> Path | None:
    if lp_path is None:
        return None
    solution_path = Path(base_solution_dir).expanduser().resolve() / f"{lp_artifact_stem(lp_path)}.sol"
    if not solution_path.exists():
        return None
    return solution_path


def _resolve_tuned_param_path(lp_path: Path | None, tuned_param_dir: str | Path) -> Path | None:
    if lp_path is None:
        return None
    prm_path = Path(tuned_param_dir).expanduser().resolve() / f"{lp_artifact_stem(lp_path)}.prm"
    if not prm_path.exists():
        return None
    return prm_path


def _format_time_limit_tag(time_limit: int | float) -> str:
    numeric = float(time_limit)
    if numeric.is_integer():
        return f"{int(numeric)}s"
    return f"{str(numeric).replace('.', 'p')}s"


def _coerce_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return decoded
    return []


def _resolve_reference_artifact(spec: ProblemSpec, case: GroundTruthCase) -> GroundTruthArtifact | None:
    if spec.ground_truth is None:
        return None
    artifact_name = case.reference_artifact
    if artifact_name:
        for artifact in spec.ground_truth.artifacts:
            if artifact.name == artifact_name:
                return artifact
        return None
    if len(spec.ground_truth.artifacts) == 1:
        return spec.ground_truth.artifacts[0]
    return None


def _find_case(cases: list[GroundTruthCase], case_id: str) -> GroundTruthCase | None:
    for case in cases:
        if case.case_id == case_id:
            return case
    return None


def _is_optimal(status: Any) -> bool:
    if isinstance(status, str):
        return status.upper() == "OPTIMAL" or status == str(int(GRB.OPTIMAL))
    if isinstance(status, (int, float)):
        return int(status) == int(GRB.OPTIMAL)
    return False


def _infeasibility_label(metrics: dict[str, Any]) -> str | None:
    if _first_finite_number(metrics.get("objective")) is not None:
        return None
    status = metrics.get("status")
    if isinstance(status, str):
        return _INFEASIBILITY_STATUSES.get(status.strip().upper())
    if isinstance(status, (int, float)):
        return _INFEASIBILITY_STATUSES.get(int(status))
    return None


def _schedule_match(candidate_schedule: Any, reference_schedule: Any) -> bool | None:
    left = _coerce_schedule(candidate_schedule)
    right = _coerce_schedule(reference_schedule)
    if not left or not right:
        return None
    return left == right


def _extract_candidate_schedule(result_payload: dict[str, Any]) -> dict[int, int] | None:
    step = _result_step_payload(result_payload)
    candidates = [
        step.get("solution") if step else None,
        result_payload.get("new_solution"),
        result_payload.get("solution"),
    ]
    for candidate in candidates:
        schedule = _coerce_schedule(candidate)
        if schedule:
            return schedule
    return None


def _coerce_schedule(raw_schedule: Any) -> dict[int, int] | None:
    if isinstance(raw_schedule, str) and raw_schedule.strip():
        with contextlib.suppress(json.JSONDecodeError):
            return _coerce_schedule(json.loads(raw_schedule))
    if not isinstance(raw_schedule, dict):
        return None
    schedule: dict[int, int] = {}
    for raw_block, raw_slot in raw_schedule.items():
        try:
            block = int(raw_block)
            slot = int(raw_slot)
        except (TypeError, ValueError):
            continue
        schedule[block] = slot
    return dict(sorted(schedule.items())) or None


def _resolve_planner_mode(result_payload: dict[str, Any]) -> str:
    candidates = [
        result_payload.get("planner_mode"),
        (result_payload.get("input") or {}).get("planner_mode"),
        (result_payload.get("input") or {}).get("config", {}).get("planner_mode"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return ""


def _has_patch_semantics_payload(result_payload: dict[str, Any]) -> bool:
    return _resolve_planner_mode(result_payload) != "codeedit" and bool(_extract_chosen_patches(result_payload))


def _extract_chosen_patches(result_payload: dict[str, Any]) -> list[Any]:
    step = _result_step_payload(result_payload)
    if step and isinstance(step.get("chosen_patches"), list):
        return list(step.get("chosen_patches") or [])
    if isinstance(result_payload.get("chosen_patches"), list):
        return list(result_payload.get("chosen_patches") or [])
    if isinstance(result_payload.get("chosen_patch"), dict) and result_payload.get("chosen_patch"):
        return [dict(result_payload.get("chosen_patch") or {})]
    trace_patches = _extract_trace_normalized_patches(result_payload)
    if trace_patches:
        return trace_patches
    return []


def _extract_trace_normalized_patches(result_payload: dict[str, Any]) -> list[Any]:
    trace_dir = result_payload.get("trace_dir")
    if not isinstance(trace_dir, str) or not trace_dir.strip():
        return []
    normalized_actions_path = Path(trace_dir).expanduser() / "normalized_actions.json"
    if not normalized_actions_path.exists():
        return []
    try:
        payload = json.loads(normalized_actions_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    if payload and all(isinstance(item, dict) and "op" in item for item in payload):
        return [dict(item) for item in payload]
    for item in payload:
        if isinstance(item, dict) and item.get("action_kind") == "patch" and isinstance(item.get("actions"), list):
            return [dict(action) for action in item.get("actions") or [] if isinstance(action, dict)]
        if isinstance(item, list):
            patches = [dict(action) for action in item if isinstance(action, dict) and "op" in action]
            if patches:
                return patches
    return []


def _resolve_planner_annotations(result_payload: dict[str, Any]) -> dict[str, Any]:
    step = _result_step_payload(result_payload)
    if step:
        planner_output = dict(step.get("planner_output") or {})
        annotations = planner_output.get("annotations")
        if isinstance(annotations, dict):
            return dict(annotations)
    planner_output = dict(result_payload.get("planner_output") or {})
    annotations = planner_output.get("annotations")
    if isinstance(annotations, dict):
        return dict(annotations)
    return {}


def _resolve_delta_metadata(
    result_payload: dict[str, Any],
    prompt_id: str,
    base_model,
) -> dict[str, Any]:
    annotations = _resolve_planner_annotations(result_payload)
    delta_metadata = dict(annotations.get("delta_metadata") or {})
    delta_metadata.setdefault("prompt_id", prompt_id)
    if prompt_id == "P3":
        delta_metadata.setdefault("prompt_params", exam_prompt_params(prompt_id, context=base_model.parameters))
    return delta_metadata


def _resolve_delta_text(result_payload: dict[str, Any], case: GroundTruthCase) -> str:
    step = _result_step_payload(result_payload)
    candidates = [
        result_payload.get("delta_text"),
        result_payload.get("delta_request"),
        (result_payload.get("input") or {}).get("delta_text"),
        step.get("delta_request") if step else None,
        case.delta_text,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return case.delta_text


def _result_step_payload(result_payload: dict[str, Any]) -> dict[str, Any]:
    result = result_payload.get("result")
    if isinstance(result, dict):
        steps = result.get("steps")
        if isinstance(steps, list) and steps:
            last = steps[-1]
            if isinstance(last, dict):
                return last
    return {}


def _instance_id_from_path(instance_dir: str | Path) -> str:
    lp_path = Path(instance_dir) / "model.lp"
    if not lp_path.exists():
        return ""
    with contextlib.suppress(Exception):
        parsed = parse_lp_instance(lp_path)
        return str(parsed.get("instance_id") or "")
    return ""


def _effective_pair_count(model, pair: tuple[int, int]) -> float:
    normalized_pair = _coerce_pair_key(pair)
    if normalized_pair is None:
        return 0.0
    overrides = dict(model.parameters.get("p") or {})
    override_value = _get_pair_value(overrides, normalized_pair)
    if isinstance(override_value, (int, float)):
        return float(override_value)
    base_values = dict(model.parameters.get("pair_counts") or {})
    base_value = _get_pair_value(base_values, normalized_pair)
    if isinstance(base_value, (int, float)):
        return float(base_value)
    return 0.0


def _get_pair_value(values: dict[Any, Any], pair: tuple[int, int]) -> Any:
    if pair in values:
        return values[pair]
    for raw_key, value in values.items():
        if _coerce_pair_key(raw_key) == pair:
            return value
    return None


def _penultimate_evening_slot(model) -> int:
    slots = sorted(int(slot) for slot in model.parameters.get("blocks", []))
    slots_per_day = int(model.parameters.get("slots_per_day", 0) or 0)
    if not slots or slots_per_day <= 0:
        raise RuntimeError("Exam structured model is missing slot metadata.")
    slot_times = [str(label).strip().lower() for label in (model.parameters.get("slot_times") or [])]
    evening_index = len(slot_times) - 1 if slot_times else slots_per_day - 1
    evening_slots = [slot for slot in slots if (slot - 1) % slots_per_day == evening_index]
    if len(evening_slots) >= 2:
        return evening_slots[-2]
    if evening_slots:
        return evening_slots[-1]
    raise RuntimeError("Exam structured model could not resolve an evening slot.")


def _slots_for_day(day_value: int, model) -> list[int] | None:
    slots_per_day = int(model.parameters.get("slots_per_day", 0) or 0)
    blocks = [int(slot) for slot in model.parameters.get("blocks", [])]
    if day_value <= 0 or slots_per_day <= 0 or not blocks:
        return None
    start = (int(day_value) - 1) * slots_per_day + 1
    end = min(max(blocks), start + slots_per_day - 1)
    if start > end:
        return None
    return list(range(start, end + 1))


def _normalize_key(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(int(value))
    return str(value)


def _safe_model_float(model: gp.Model, attr: str) -> float | None:
    with contextlib.suppress(Exception):
        value = getattr(model, attr)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _same_number_or_none(left: Any, right: Any) -> bool:
    left_num = _first_number(left)
    right_num = _first_number(right)
    if left_num is None or right_num is None:
        return left_num is None and right_num is None
    return math.isclose(left_num, right_num, rel_tol=0.0, abs_tol=1e-9)


def _same_text_or_none(left: Any, right: Any) -> bool:
    left_text = str(left).strip() if left not in {None, ""} else ""
    right_text = str(right).strip() if right not in {None, ""} else ""
    return left_text == right_text


def _same_bool_or_none(left: Any, right: Any) -> bool:
    left_bool = _coerce_bool(left)
    right_bool = _coerce_bool(right)
    if left_bool is None or right_bool is None:
        return left_bool is None and right_bool is None
    return left_bool == right_bool


def _same_number(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    return False


def _first_solver_status(*values: Any) -> Any:
    for value in values:
        if _is_solver_status(value):
            return value
    return None


def _is_solver_status(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        return text.isdigit() or text.upper() in {"OPTIMAL", "TIME_LIMIT", "INFEASIBLE", "INF_OR_UNBD", "UNBOUNDED"}
    return False


def _first_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _first_finite_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None
