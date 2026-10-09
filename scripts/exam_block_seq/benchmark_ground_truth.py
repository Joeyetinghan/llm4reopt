#!/usr/bin/env python3
"""Solve exam block-sequencing ground-truth cases with the reference module's gold edits."""
from __future__ import annotations

import argparse
import contextlib
import csv
from datetime import datetime
import importlib.util
import io
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import gurobipy as gp
from gurobipy import GRB

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from framework.registry import load_problem
from framework.utils.gurobi_tuning import load_prm_params, lp_artifact_stem
from problems.exam_block_seq.warm_start import apply_exam_warm_start_payload, resolve_exam_warm_start_payload
from problems.exam_block_seq import ground_truth as exam_ground_truth
from problems.exam_block_seq.ground_truth.evaluator import (
    _build_structured_model_from_reference_state,
    _coerce_instance_dir,
)
from problems.exam_block_seq.paths import RUNS_ROOT, SOLUTIONS_DIR, TUNED_PARAMS_DIR, format_time_limit_tag


DEFAULT_TIME_LIMIT = 86400
DEFAULT_MIP_GAP = 1e-2
DEFAULT_THREADS = 6
DEFAULT_WARM_START_MODE = "base+heuristic"

STATUS_LABELS = {
    int(GRB.OPTIMAL): "OPTIMAL",
    int(GRB.INFEASIBLE): "INFEASIBLE",
    int(GRB.INF_OR_UNBD): "INF_OR_UNBD",
    int(GRB.UNBOUNDED): "UNBOUNDED",
    int(GRB.TIME_LIMIT): "TIME_LIMIT",
}


def status_label(status_code: Any) -> str:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return str(status_code)
    return STATUS_LABELS.get(code, str(code))


def load_exam_spec():
    spec, _ = load_problem(problem="exam_block_seq", load_runtime_data=False)
    return spec


def default_cases(spec) -> list[tuple[str, str]]:
    if spec.ground_truth is None:
        return []
    pairs: list[tuple[str, str]] = []
    for case in spec.ground_truth.cases:
        if not case.active:
            continue
        instance_id = str(case.metadata.get("instance_id", "")).strip()
        prompt_id = str(case.metadata.get("prompt_id", "")).strip()
        if instance_id and prompt_id:
            pairs.append((instance_id, prompt_id))
    return pairs


def parse_cases(
    spec,
    instance: str | None,
    prompt: str | None,
    cases: list[str] | None,
) -> list[tuple[str, str]]:
    if instance and prompt:
        return [(instance, prompt)]
    if cases:
        parsed = []
        for case in cases:
            inst, prm = case.split(":", 1)
            parsed.append((inst, prm))
        return parsed
    return default_cases(spec)


def solve_ground_truth_case(
    spec,
    instance_id: str,
    prompt_id: str,
    time_limit: int,
    output_dir: Path,
    mip_gap: float | None = None,
    threads: int | None = None,
    trace_dir: Path | None = None,
    warm_start_mode: str = DEFAULT_WARM_START_MODE,
    base_solution_dir: str | Path = SOLUTIONS_DIR,
    use_tuned_params: bool = True,
    tuned_param_dir: str | Path = TUNED_PARAMS_DIR,
) -> dict[str, Any]:
    reference_script = _resolve_reference_script(spec, instance_id, prompt_id)
    module, script_output = _load_reference_module(
        reference_script=reference_script,
        package_root=spec.metadata.package_root,
        expected_instance_id=instance_id,
        expected_prompt_id=prompt_id,
    )

    model = getattr(module, "m", None)
    if model is None or not isinstance(model, gp.Model):
        raise RuntimeError(f"Reference script did not expose a gurobipy model named 'm': {reference_script}")

    prompt_task = str(getattr(module, "PROMPT_TASK", ""))
    reference_changes = list(getattr(module, "REFERENCE_CHANGES", []))
    assumptions = list(getattr(module, "ASSUMPTIONS", []))
    state = getattr(module, "state", None)
    instance_dir = _coerce_instance_dir(state)
    lp_path = instance_dir / "model.lp" if instance_dir is not None else None
    tuned_param_path = _resolve_tuned_param_path(lp_path, tuned_param_dir) if use_tuned_params and lp_path else None
    use_base_warm_start = warm_start_mode in {"base", "base+heuristic"}
    use_heuristic_warm_start = warm_start_mode in {"heuristic", "base+heuristic"}
    warm_start_path = _resolve_saved_solution_path(lp_path, base_solution_dir) if use_base_warm_start and lp_path else None
    heuristic_warm_start = None
    warm_start_payload = None
    resolved_warm_start_mode = "none"
    if state is not None and lp_path is not None:
        structured_for_warm_start = _build_structured_model_from_reference_state(state, lp_path)
        structured_for_warm_start.parameters["disable_default_warm_start"] = not use_heuristic_warm_start
        if warm_start_path is not None:
            structured_for_warm_start.parameters["warm_start"] = {"sol_path": str(warm_start_path)}
        warm_start_payload, resolved_warm_start_mode = resolve_exam_warm_start_payload(structured_for_warm_start)
        if isinstance(warm_start_payload, dict):
            starts = warm_start_payload.get("mip_starts")
            if isinstance(starts, list):
                heuristic_warm_start = next(
                    (start for start in starts if isinstance(start, dict) and isinstance(start.get("var_starts"), dict)),
                    None,
                )
            elif isinstance(warm_start_payload.get("var_starts"), dict):
                heuristic_warm_start = warm_start_payload

    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        with (trace_dir / "run_input.json").open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "instance_id": instance_id,
                    "prompt_id": prompt_id,
                    "reference_script": str(reference_script),
                    "prompt_task": prompt_task,
                    "reference_changes": reference_changes,
                    "assumptions": assumptions,
                    "time_limit_s": time_limit,
                    "mip_gap_limit": mip_gap,
                    "threads": threads,
                    "requested_warm_start_mode": warm_start_mode,
                    "warm_start_path": str(warm_start_path) if warm_start_path is not None else None,
                    "warm_start_mode": resolved_warm_start_mode,
                    "heuristic_warm_start_available": heuristic_warm_start is not None,
                    "use_tuned_params": use_tuned_params,
                    "tuned_param_path": str(tuned_param_path) if tuned_param_path is not None else None,
                },
                fh,
                indent=2,
                sort_keys=True,
            )
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = str(trace_dir / "ground_truth_solver.log")
    else:
        model.Params.OutputFlag = 0
    if tuned_param_path is not None:
        for name, value in load_prm_params(tuned_param_path).items():
            if value is None:
                continue
            model.setParam(str(name), value)
    model.Params.TimeLimit = float(time_limit)
    if mip_gap is not None:
        model.Params.MIPGap = float(mip_gap)
    if threads is not None:
        model.Params.Threads = int(threads)
    apply_exam_warm_start_payload(model, warm_start_payload)

    wall_start = time.time()
    model.optimize()
    if model.Status == GRB.INF_OR_UNBD:
        model.Params.Presolve = 0
        model.optimize()
    wall_time = time.time() - wall_start

    output_dir.mkdir(parents=True, exist_ok=True)
    has_solution = model.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT) and model.SolCount > 0
    objective = float(model.ObjVal) if has_solution else None
    try:
        obj_bound = float(model.ObjBound)
    except Exception:
        obj_bound = None
    gap = float(model.MIPGap) if has_solution and model.IsMIP else None
    schedule = {}
    if has_solution and isinstance(state, dict):
        from problems.exam_block_seq.solution import extract_block_assignments_from_model

        schedule = extract_block_assignments_from_model(
            model,
            [int(block) for block in state.get("blocks", [])],
        )
    execution_label = _reference_execution_label(
        warm_start_mode=resolved_warm_start_mode,
        use_tuned_params=tuned_param_path is not None,
    )
    sol_path = output_dir / f"{instance_id}_{prompt_id}.sol"
    if has_solution:
        model.write(str(sol_path))

    row = {
        "case_id": f"{instance_id}_{prompt_id}",
        "instance_id": instance_id,
        "prompt_id": prompt_id,
        "status_code": int(model.Status),
        "status": status_label(model.Status),
        "sol_count": int(model.SolCount),
        "objective": objective,
        "obj_bound": obj_bound,
        "mip_gap": gap,
        "wall_time_s": round(wall_time, 2),
        "runtime_attr_s": round(float(model.Runtime), 2),
        "time_limit_s": time_limit,
        "mip_gap_limit": mip_gap,
        "threads": threads,
        "execution_label": execution_label,
        "warm_start_used": warm_start_payload is not None,
        "warm_start_path": str(warm_start_path) if warm_start_path is not None else "",
        "warm_start_mode": resolved_warm_start_mode,
        "heuristic_warm_start_used": heuristic_warm_start is not None,
        "use_tuned_params": tuned_param_path is not None,
        "tuned_param_path": str(tuned_param_path) if tuned_param_path is not None else "",
        "schedule": json.dumps(schedule, sort_keys=True),
        "num_vars": int(model.NumVars),
        "num_constraints": int(model.NumConstrs),
        "num_binary_vars": int(model.NumBinVars),
        "sol_path": str(sol_path) if has_solution else "",
        "reference_script": str(reference_script),
        "prompt_task": prompt_task,
        "modification_count": len(reference_changes),
        "modification_details": json.dumps(reference_changes),
        "assumptions": json.dumps(assumptions),
        "script_output": script_output,
        "error": "",
    }
    if trace_dir is not None:
        with (trace_dir / "result.json").open("w", encoding="utf-8") as fh:
            json.dump(row, fh, indent=2, sort_keys=True)
    write_outputs([row], output_dir)
    if hasattr(model, "dispose"):
        model.dispose()
    return row


def write_outputs(rows: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "summary.csv"
    json_path = output_dir / "summary.json"

    fieldnames = [
        "case_id",
        "instance_id",
        "prompt_id",
        "status",
        "status_code",
        "sol_count",
        "objective",
        "obj_bound",
        "mip_gap",
        "wall_time_s",
        "runtime_attr_s",
        "time_limit_s",
        "mip_gap_limit",
        "threads",
        "execution_label",
        "warm_start_used",
        "warm_start_path",
        "warm_start_mode",
        "heuristic_warm_start_used",
        "use_tuned_params",
        "tuned_param_path",
        "schedule",
        "sol_path",
        "modification_count",
        "modification_details",
        "assumptions",
        "prompt_task",
        "reference_script",
        "num_vars",
        "num_binary_vars",
        "num_constraints",
        "script_output",
        "error",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)

    print(f"\nWrote {csv_path}")
    print(f"Wrote {json_path}")


def _resolve_reference_script(spec, instance_id: str, prompt_id: str) -> Path:
    if spec.ground_truth is None or not spec.ground_truth.artifacts:
        raise FileNotFoundError("Exam problem does not declare a ground-truth reference artifact.")
    artifact_root = spec.ground_truth.artifacts[0].path
    return exam_ground_truth._resolve_reference_script(artifact_root, instance_id, prompt_id)


def _load_reference_module(
    *,
    reference_script: Path,
    package_root: Path,
    expected_instance_id: str,
    expected_prompt_id: str,
) -> tuple[Any, str]:
    module_name = f"_exam_ground_truth_runner_{reference_script.stem}_{uuid.uuid4().hex}"
    spec_obj = importlib.util.spec_from_file_location(module_name, reference_script)
    if spec_obj is None or spec_obj.loader is None:
        raise RuntimeError(f"Unable to import reference script: {reference_script}")

    module = importlib.util.module_from_spec(spec_obj)
    repo_root = package_root.parents[1]
    added_repo_root = False
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
        added_repo_root = True
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            spec_obj.loader.exec_module(module)
            module = module.build_reference(expected_instance_id, expected_prompt_id)
    finally:
        if added_repo_root:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(repo_root))

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
    return module, captured.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark exam ground-truth reoptimization cases")
    parser.add_argument("--instance", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--cases", nargs="*", default=None, help="Optional list like I1:P1 I1:P6")
    parser.add_argument("--time-limit", type=int, default=DEFAULT_TIME_LIMIT)
    parser.add_argument("--mip-gap", type=float, default=DEFAULT_MIP_GAP)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--trace-root", default=None, help="Optional directory for per-case trace artifacts")
    parser.add_argument(
        "--warm-start-mode",
        choices=["base", "heuristic", "base+heuristic", "none"],
        default=None,
        help="Warm-start mode for GT solve. Defaults to base+heuristic.",
    )
    parser.add_argument("--disable-warm-start", action="store_true", help="Disable loading the saved base .sol as a MIP start")
    parser.add_argument("--disable-tuned-params", action="store_true", help="Disable loading the tuned .prm for the instance")
    parser.add_argument("--base-solution-dir", default=str(SOLUTIONS_DIR))
    parser.add_argument("--tuned-param-dir", default=str(TUNED_PARAMS_DIR))
    args = parser.parse_args()

    spec = load_exam_spec()
    cases = parse_cases(spec, args.instance, args.prompt, args.cases)
    output_root = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else RUNS_ROOT / "ground_truth" / format_time_limit_tag(args.time_limit)
    )
    requested_warm_start_mode = _requested_gt_warm_start_mode(args.warm_start_mode, disable_warm_start=args.disable_warm_start)
    run_trace_root = None
    if args.trace_root:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_trace_root = Path(args.trace_root).expanduser().resolve() / f"{run_id}_ground_truth"
        run_trace_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    print(f"Running exam ground-truth benchmark for {len(cases)} case(s)")
    print(f"Time limit: {args.time_limit}s")
    print(f"MIP gap: {args.mip_gap}")
    print(f"Threads: {args.threads}")
    print(f"Warm start mode: {requested_warm_start_mode}")
    print(f"Tuned params: {not args.disable_tuned_params}")

    for instance_id, prompt_id in cases:
        print(f"\n[{instance_id}, {prompt_id}] solving ground truth...")
        try:
            row = solve_ground_truth_case(
                spec=spec,
                instance_id=instance_id,
                prompt_id=prompt_id,
                time_limit=args.time_limit,
                output_dir=output_root / instance_id / prompt_id,
                mip_gap=args.mip_gap,
                threads=args.threads,
                trace_dir=run_trace_root / f"{instance_id}_{prompt_id}" if run_trace_root is not None else None,
                warm_start_mode=requested_warm_start_mode,
                base_solution_dir=args.base_solution_dir,
                use_tuned_params=not args.disable_tuned_params,
                tuned_param_dir=args.tuned_param_dir,
            )
        except Exception as exc:
            row = {
                "case_id": f"{instance_id}_{prompt_id}",
                "instance_id": instance_id,
                "prompt_id": prompt_id,
                "status": "ERROR",
                "status_code": "",
                "sol_count": 0,
                "objective": None,
                "obj_bound": None,
                "mip_gap": None,
                "wall_time_s": None,
                "runtime_attr_s": None,
                "time_limit_s": args.time_limit,
                "mip_gap_limit": args.mip_gap,
                "threads": args.threads,
                "execution_label": _reference_execution_label(
                    warm_start_mode=requested_warm_start_mode,
                    use_tuned_params=not args.disable_tuned_params,
                ),
                "warm_start_used": False,
                "warm_start_path": "",
                "warm_start_mode": "none",
                "heuristic_warm_start_used": False,
                "use_tuned_params": not args.disable_tuned_params,
                "tuned_param_path": "",
                "schedule": "",
                "sol_path": "",
                "modification_count": None,
                "modification_details": "",
                "assumptions": "",
                "prompt_task": "",
                "reference_script": "",
                "num_vars": None,
                "num_binary_vars": None,
                "num_constraints": None,
                "script_output": "",
                "error": str(exc),
            }
        rows.append(row)
        print(
            f"  status={row['status']}  obj={row['objective']}  gap={row['mip_gap']}  "
            f"runtime={row['runtime_attr_s']}s  sol={row['sol_path']}"
        )

    write_outputs(rows, output_root)


def _requested_gt_warm_start_mode(warm_start_mode: str | None, *, disable_warm_start: bool) -> str:
    if warm_start_mode is not None:
        return str(warm_start_mode)
    if disable_warm_start:
        return "none"
    return DEFAULT_WARM_START_MODE


def _reference_execution_label(*, warm_start_mode: str, use_tuned_params: bool) -> str:
    tokens: list[str] = []
    if warm_start_mode in {"base", "base+heuristic"}:
        tokens.append("direct")
    if warm_start_mode in {"heuristic", "base+heuristic"}:
        tokens.append("heuristic")
    if use_tuned_params:
        tokens.append("tuned")
    return "+".join(tokens) if tokens else "scratch"


def _resolve_saved_solution_path(lp_path: Path | None, base_solution_dir: str | Path) -> Path | None:
    if lp_path is None:
        return None
    solution_path = Path(base_solution_dir).expanduser().resolve() / f"{lp_artifact_stem(lp_path)}.sol"
    if not solution_path.exists():
        raise FileNotFoundError(f"Base solution file not found: {solution_path}")
    return solution_path


def _resolve_tuned_param_path(lp_path: Path | None, tuned_param_dir: str | Path) -> Path | None:
    if lp_path is None:
        return None
    prm_path = Path(tuned_param_dir).expanduser().resolve() / f"{lp_artifact_stem(lp_path)}.prm"
    if not prm_path.exists():
        raise FileNotFoundError(f"Tuned parameter file not found: {prm_path}")
    return prm_path


if __name__ == "__main__":
    main()
