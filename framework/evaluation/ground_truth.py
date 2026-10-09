"""Ground-truth evaluator utilities."""

from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from typing import Any

from framework.core import GroundTruthCheckResult, GroundTruthEvaluator, ProblemSpec


def load_ground_truth_evaluator(spec: ProblemSpec) -> GroundTruthEvaluator:
    if spec.ground_truth is None:
        raise ValueError(f"Problem '{spec.metadata.problem_id}' does not declare ground truth")

    evaluator_path = spec.ground_truth.evaluator_path
    if ":" not in evaluator_path:
        raise ValueError(f"Invalid ground truth evaluator path: {evaluator_path}")

    module_name, class_name = evaluator_path.split(":", 1)
    module = importlib.import_module(module_name)
    evaluator_cls = getattr(module, class_name)
    evaluator = evaluator_cls()
    if not isinstance(evaluator, GroundTruthEvaluator):
        raise TypeError(f"Ground truth evaluator must implement GroundTruthEvaluator: {evaluator_path}")
    return evaluator


def discover_result_files(results_dir: str | Path) -> list[Path]:
    root = Path(results_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Results directory not found: {root}")
    return [
        path
        for path in sorted(root.rglob("*.json"))
        if path.is_file() and not _skip_result_file(path)
    ]


def load_result_payload(result_path: str | Path) -> dict[str, Any]:
    path = Path(result_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    payload = dict(payload)
    payload["__result_path__"] = str(path)
    payload["__result_name__"] = path.name
    payload["__result_stem__"] = path.stem
    return payload


def evaluate_result_payload(
    spec: ProblemSpec,
    result_payload: dict[str, Any],
    *,
    explicit_case_id: str | None = None,
    evaluator: GroundTruthEvaluator | None = None,
    reference_policy: str = "off",
) -> GroundTruthCheckResult:
    evaluator = evaluator or load_ground_truth_evaluator(spec)

    if spec.ground_truth is None:
        raise ValueError(f"Problem '{spec.metadata.problem_id}' has no ground truth bundle")

    case = evaluator.match_case(spec, result_payload, explicit_case_id=explicit_case_id)
    if case is not None:
        return evaluator.evaluate_case(
            spec,
            case,
            result_payload,
            reference_policy=reference_policy,
        )

    if spec.ground_truth.status != "active" and not spec.ground_truth.cases:
        return GroundTruthCheckResult(
            problem_id=spec.metadata.problem_id,
            case_id=explicit_case_id,
            result_path=str(result_payload.get("__result_path__")) if result_payload.get("__result_path__") else None,
            status=spec.ground_truth.status,
            matched_case=False,
            matches_ground_truth=None,
            checks={"pending_reference_artifact": True},
            details={
                "message": "Ground truth exists as a scaffold but no active cases are available yet.",
            },
        )

    return GroundTruthCheckResult(
        problem_id=spec.metadata.problem_id,
        case_id=explicit_case_id,
        result_path=str(result_payload.get("__result_path__")) if result_payload.get("__result_path__") else None,
        status="unmatched_case",
        matched_case=False,
        matches_ground_truth=None,
        checks={},
        details={
            "available_cases": [case.case_id for case in evaluator.load_cases(spec)],
            "payload_keys": sorted(result_payload.keys()),
        },
    )


def evaluate_results_dir(
    spec: ProblemSpec,
    results_dir: str | Path,
    *,
    explicit_case_id: str | None = None,
    include_unmatched: bool = False,
    reference_policy: str = "off",
) -> list[GroundTruthCheckResult]:
    evaluator = load_ground_truth_evaluator(spec)
    rows: list[GroundTruthCheckResult] = []
    for result_file in discover_result_files(results_dir):
        payload = load_result_payload(result_file)
        row = evaluate_result_payload(
            spec,
            payload,
            explicit_case_id=explicit_case_id,
            evaluator=evaluator,
            reference_policy=reference_policy,
        )
        if include_unmatched or row.matched_case or row.status != "unmatched_case":
            rows.append(row)
    return rows


def summarize_ground_truth(rows: list[GroundTruthCheckResult]) -> dict[str, Any]:
    total = len(rows)
    matched = sum(1 for row in rows if row.matched_case)
    passed = sum(1 for row in rows if row.matches_ground_truth is True)
    failed = sum(1 for row in rows if row.matches_ground_truth is False)
    pending = sum(1 for row in rows if row.status in {"pending_reference_artifact", "pending", "skipped"})
    partial = sum(1 for row in rows if row.status == "partial")
    unmatched = sum(1 for row in rows if row.status == "unmatched_case")
    return {
        "total": total,
        "matched": matched,
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "partial": partial,
        "unmatched": unmatched,
    }


def write_ground_truth_csv(rows: list[GroundTruthCheckResult], path: str | Path) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payloads = [row.to_dict() for row in rows]
    fieldnames: list[str] = []
    for row in payloads:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payloads)
    return output_path


def _skip_result_file(path: Path) -> bool:
    name = path.name
    if name.startswith("summary"):
        return True
    if name.endswith("_log.json"):
        return True
    if name in {"evaluation.json"}:
        return True
    return False
