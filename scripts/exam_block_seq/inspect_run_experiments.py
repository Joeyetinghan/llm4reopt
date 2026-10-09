#!/usr/bin/env python3
"""Merge and inspect exam campaign outputs."""
from __future__ import annotations

from collections import Counter
import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def inspect_campaign(campaign_root: str | Path) -> dict[str, Any]:
    root = _resolve_repo_path(campaign_root)
    results_root = root / "results"
    reports_root = root / "reports"
    rows = collect_runs(results_root)
    report_paths = write_reports(rows, reports_root)
    return {
        "campaign_root": str(root),
        "results_root": str(results_root),
        "reports_root": str(reports_root),
        "rows": rows,
        "report_paths": report_paths,
    }


def collect_runs(results_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result_path in sorted(results_root.rglob("*.json")):
        if _should_skip_json(result_path):
            continue
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        row = _row_from_payload(result_path, payload)
        if row is not None:
            rows.append(row)
    return rows


def write_reports(rows: list[dict[str, Any]], reports_root: Path) -> dict[str, str]:
    reports_root.mkdir(parents=True, exist_ok=True)
    merged_json = reports_root / "merged_runs.json"
    merged_csv = reports_root / "merged_runs.csv"
    summary_json = reports_root / "summary.json"

    merged_json.write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_csv(rows, merged_csv)
    summary_json.write_text(
        json.dumps(_summary_payload(rows), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "merged_json": str(merged_json),
        "merged_csv": str(merged_csv),
        "summary_json": str(summary_json),
    }


def inspect_trace_dir(trace_dir: Path | None) -> dict[str, Any]:
    result = {
        "trace_exists": False,
        "has_result_summary": False,
        "has_planner_output": False,
        "has_normalized_actions": False,
        "has_edited_files": False,
        "has_edited_diff": False,
    }
    if trace_dir is None:
        return result
    if not trace_dir.exists():
        return result

    result["trace_exists"] = True
    result["has_result_summary"] = (trace_dir / "result_summary.md").exists() or (trace_dir / "result_summary.txt").exists()
    result["has_planner_output"] = (trace_dir / "planner_output.json").exists()
    result["has_normalized_actions"] = (trace_dir / "normalized_actions.json").exists()
    result["has_edited_files"] = (trace_dir / "edited_files.json").exists()
    result["has_edited_diff"] = (trace_dir / "edited_diff.patch").exists()
    return result


def _row_from_payload(result_path: Path, payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    input_payload = dict(payload.get("input") or {})
    result_payload = dict(payload.get("result") or {})
    steps = list(result_payload.get("steps") or [])
    final_step = steps[-1] if steps and isinstance(steps[-1], dict) else {}
    solve_meta = dict(final_step.get("solve_meta") or {})
    strategy_selection = dict(final_step.get("strategy_selection") or {})

    instance = payload.get("instance_id") or payload.get("instance")
    prompt = payload.get("prompt_id") or payload.get("prompt") or input_payload.get("prompt_id")
    model = payload.get("model") or payload.get("llm_model") or input_payload.get("model")
    if not (instance and prompt and model):
        return None

    trace_dir_value = payload.get("trace_dir") or final_step.get("artifacts", {}).get("trace_dir")
    trace_dir = Path(str(trace_dir_value)).expanduser() if trace_dir_value not in {None, ""} else None
    trace_info = inspect_trace_dir(trace_dir)

    execution_label = payload.get("execution_label")
    if execution_label in {None, ""}:
        execution_label = strategy_selection.get("execution_label", final_step.get("strategy", ""))

    objective = payload.get("objective")
    if objective in {None, ""}:
        objective = final_step.get("objective")
    obj_bound = payload.get("obj_bound")
    if obj_bound in {None, ""}:
        obj_bound = solve_meta.get("obj_bound")
    mip_gap = payload.get("mip_gap")
    if mip_gap in {None, ""}:
        mip_gap = solve_meta.get("mip_gap", solve_meta.get("gap"))

    return {
        "run_id": str(payload.get("run_id") or result_path.stem),
        "instance": str(instance),
        "prompt": str(prompt),
        "model": str(model),
        "planner_mode": str(payload.get("planner_mode") or input_payload.get("planner_mode") or ""),
        "strategy": str(payload.get("strategy") or final_step.get("strategy") or input_payload.get("strategy") or ""),
        "strategy_policy": str(
            payload.get("strategy_policy")
            or strategy_selection.get("policy_name")
            or input_payload.get("strategy_policy")
            or ""
        ),
        "execution_label": str(execution_label or ""),
        "status": str(payload.get("status") or ""),
        "objective": objective,
        "obj_bound": obj_bound,
        "mip_gap": mip_gap,
        "failure_stage": payload.get("failure_stage"),
        "failure_label": payload.get("failure_label"),
        "failure_detail": payload.get("failure_detail"),
        "trace_dir": str(trace_dir) if trace_dir is not None else "",
        "result_path": str(result_path),
        **trace_info,
    }


def _summary_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status") or "") for row in rows)
    grouped = Counter(
        (
            str(row.get("planner_mode") or ""),
            str(row.get("model") or ""),
            str(row.get("strategy") or ""),
            str(row.get("execution_label") or ""),
            str(row.get("status") or ""),
            str(row.get("failure_label") or ""),
        )
        for row in rows
    )
    return {
        "total_runs": len(rows),
        "ok_runs": status_counts.get("ok", 0),
        "error_runs": status_counts.get("error", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "group_counts": [
            {
                "planner_mode": planner_mode,
                "model": model,
                "strategy": strategy,
                "execution_label": execution_label,
                "status": status,
                "failure_label": failure_label,
                "count": count,
            }
            for (planner_mode, model, strategy, execution_label, status, failure_label), count in sorted(grouped.items())
        ],
    }


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _should_skip_json(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    if name in {"summary.json"}:
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect selector-based exam run campaigns")
    parser.add_argument("--campaign-root", required=True, help="Campaign root under runs/exam/")
    args = parser.parse_args()

    summary = inspect_campaign(args.campaign_root)
    rows = summary["rows"]
    report_paths = summary["report_paths"]
    print(f"Campaign root: {summary['campaign_root']}")
    print(f"Runs found: {len(rows)}")
    if rows:
        grouped = _summary_payload(rows)["group_counts"]
        for item in grouped:
            failure = f" failure={item['failure_label']}" if item["failure_label"] else ""
            print(
                f"{item['planner_mode']} {item['model']} {item['strategy']} "
                f"label={item['execution_label']} status={item['status']} "
                f"count={item['count']}{failure}"
            )
    print(f"Merged JSON: {report_paths['merged_json']}")
    print(f"Merged CSV: {report_paths['merged_csv']}")
    print(f"Summary JSON: {report_paths['summary_json']}")


if __name__ == "__main__":
    main()
