"""Canonical exam block sequencing package paths."""

from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PROBLEMS_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = PROBLEMS_ROOT.parent

# Inputs: the generator's raw instance files.
INSTANCES_ROOT = REPO_ROOT / "benchmark" / "raw_instances"
# Outputs: the paper's Gurobi solves (base solves, reference solves, tuning).
OUTPUTS_ROOT = REPO_ROOT / "outputs"
SOLVES_ROOT = OUTPUTS_ROOT / "solves"
SOLUTIONS_DIR = SOLVES_ROOT / "base"
GROUND_TRUTH_ROOT = SOLVES_ROOT / "reference"
TUNE_ROOT = SOLVES_ROOT / "tune"
TUNED_PARAMS_DIR = TUNE_ROOT / "params"
# New solves and runs go to the git-ignored runs/.
RUNS_ROOT = REPO_ROOT / "runs"


def format_time_limit_tag(time_limit: int | float) -> str:
    numeric = float(time_limit)
    if numeric.is_integer():
        return f"{int(numeric)}s"
    return f"{str(numeric).replace('.', 'p')}s"


