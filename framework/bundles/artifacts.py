"""Helpers for runtime-bundle base and solver artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from framework.utils import load_prm_params


SOL_OBJECTIVE_PATTERN = re.compile(r"^# Objective value = (?P<objective>[-+0-9.eE]+)$")
SOL_ASSIGNMENT_PATTERN = re.compile(r"^(?P<name>\S+)\s+(?P<value>[-+0-9.eE]+)$")
LOG_RUNTIME_PATTERN = re.compile(r"^Explored .* in (?P<runtime>[-+0-9.eE]+) seconds")
LOG_FINAL_SUMMARY_PATTERN = re.compile(
    r"^Best objective (?P<objective>[-+0-9.eE]+), best bound (?P<bound>[-+0-9.eE]+), gap (?P<gap>[-+0-9.eE]+)%$"
)
LOG_SOLUTION_COUNT_PATTERN = re.compile(r"^Solution count (?P<count>\d+):")
LOG_OPTIMAL_STATUS_PATTERN = re.compile(r"^Optimal solution found(?:\s+\(tolerance .*?\))?$")


def parse_base_solution_file(sol_path: str | Path) -> tuple[float, dict[str, float]]:
    path = Path(sol_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Base solution file not found: {path}")

    objective: float | None = None
    assignments: dict[str, float] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            objective_match = SOL_OBJECTIVE_PATTERN.match(line)
            if objective_match is not None:
                objective = float(objective_match.group("objective"))
                continue
            if line.startswith("#"):
                continue
            assignment_match = SOL_ASSIGNMENT_PATTERN.match(line)
            if assignment_match is None:
                continue
            value = float(assignment_match.group("value"))
            if abs(value) <= 1e-9:
                continue
            assignments[assignment_match.group("name")] = value

    if objective is None:
        raise ValueError(f"Missing objective header in base solution file: {path}")
    return objective, assignments


def parse_base_log_file(log_path: str | Path) -> dict[str, Any]:
    path = Path(log_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Base log file not found: {path}")

    meta: dict[str, Any] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            if line == "Time limit reached":
                meta["status"] = 9
                continue
            if LOG_OPTIMAL_STATUS_PATTERN.match(line) is not None:
                meta["status"] = 2
                continue
            runtime_match = LOG_RUNTIME_PATTERN.match(line)
            if runtime_match is not None:
                meta["runtime"] = float(runtime_match.group("runtime"))
                continue
            summary_match = LOG_FINAL_SUMMARY_PATTERN.match(line)
            if summary_match is not None:
                meta["obj_bound"] = float(summary_match.group("bound"))
                meta["mip_gap"] = float(summary_match.group("gap")) / 100.0
                continue
            solution_count_match = LOG_SOLUTION_COUNT_PATTERN.match(line)
            if solution_count_match is not None:
                meta["solution_count"] = int(solution_count_match.group("count"))

    if "runtime" not in meta:
        raise ValueError(f"Missing runtime summary in base log file: {path}")
    if "status" not in meta:
        raise ValueError(f"Missing terminal solve status in base log file: {path}")
    return meta


def load_standard_base_artifacts(bundle_root: str | Path) -> tuple[float, Any, dict[str, Any]] | None:
    root = Path(bundle_root).expanduser().resolve()
    artifacts_dir = root / "artifacts"
    sol_path = artifacts_dir / "base.sol"
    if not sol_path.exists():
        return None

    objective, solution = parse_base_solution_file(sol_path)
    meta: dict[str, Any] = {"source": "bundle_artifacts", "solution_path": str(sol_path)}
    log_path = artifacts_dir / "base.log"
    if log_path.exists():
        meta.update(parse_base_log_file(log_path))
        meta["log_path"] = str(log_path)
    return objective, solution, meta


def load_tuned_prm(bundle_root: str | Path) -> dict[str, Any] | None:
    prm_path = Path(bundle_root).expanduser().resolve() / "artifacts" / "tuned.prm"
    if not prm_path.exists():
        return None
    return load_prm_params(prm_path)
