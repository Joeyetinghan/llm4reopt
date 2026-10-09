#!/usr/bin/env python3
"""Rerun the paper's 270 Cornell exam runs from the shipped run manifests.

The manifests in ``outputs/llm_runs/manifests/`` hold the exact settings of every run:

* ``<job>.yaml`` (180): the code-edit runs and the patch-edit runs with the strategy selector,
  each run with ``python -m scripts.run_batch_experiments <job>.yaml``;
* ``<job>.replay.json`` (90): the patch-edit runs without the selector, which replay the
  patch plan of the matching selector run and re-solve it from scratch with
  ``python -m scripts.exam_block_seq.replay_patchedit_plan``.

The selector runs therefore go first. Results and traces go to
``runs/exam/paper_full_gpt_3600s/`` (git-ignored), the layout read by
``scripts/exam_block_seq/evaluate_cornell_gpt.py``. Each run solves for up to an hour and
calls the LLM API configured in ``.env``; runs are independent, so they can also be spread
over machines by passing different ``--only`` patterns.

    python -m scripts.exam_block_seq.run_paper_campaign --dry-run
    python -m scripts.exam_block_seq.run_paper_campaign --only 'I1_*_prompts_P3'
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "outputs" / "llm_runs" / "manifests"


def _replay_command(spec_path: Path) -> list[str]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    def path(value: str) -> str:
        return os.path.relpath((spec_path.parent / value).resolve(), ROOT)

    command = [sys.executable, "-m", "scripts.exam_block_seq.replay_patchedit_plan"]
    command += ["--source-results", *(path(value) for value in spec["source_results"])]
    command += ["--output-dir", path(spec["output_dir"]), "--trace-root", path(spec["trace_root"])]
    command += ["--strategy", spec["strategy"], "--solver-time-limit", str(spec["solver_time_limit"]), "--overwrite"]
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", nargs="*", default=None, help="job-name patterns, e.g. 'I1_codeedit_*' (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    args = parser.parse_args()

    def selected(path: Path) -> bool:
        job = path.name.split(".")[0]
        return args.only is None or any(fnmatch.fnmatch(job, pattern) for pattern in args.only)

    batch = [p for p in sorted(MANIFESTS.glob("*.yaml")) if selected(p)]
    replays = [p for p in sorted(MANIFESTS.glob("*.replay.json")) if selected(p)]
    commands = [[sys.executable, "-m", "scripts.run_batch_experiments", os.path.relpath(p, ROOT)] for p in batch]
    commands += [_replay_command(p) for p in replays]
    if not commands:
        raise SystemExit("no manifest matches --only")
    print(f"# {len(batch)} batch manifests, {len(replays)} replays", flush=True)

    failed = 0
    for command in commands:
        print("python " + shlex.join(command[1:]), flush=True)
        if not args.dry_run:
            failed += subprocess.run(command, cwd=ROOT).returncode != 0
    if failed:
        print(f"{failed} of {len(commands)} commands failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
