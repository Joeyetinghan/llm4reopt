#!/usr/bin/env python3
"""Materialize the large exam artifacts that are not stored in Git.

The repository ships each exam instance as compact CSV/JSON files and each base
solution as a gzipped ``.sol``. The ReOpt-LLM runtime additionally expects

* ``benchmark/raw_instances/<name>/model.lp`` (the block-sequencing MIP), and
* ``outputs/solves/base/<name>.sol`` (the uncompressed base solution).

This script rebuilds every ``model.lp`` from the CSV files with the same
gurobipy model builder used by the runtime, and unpacks the base solutions.
Building an LP needs ``gurobipy`` with a license that allows models of this size
(~0.7M binary variables); the benchmark under ``benchmark/`` does not need it.

    python -m scripts.prepare_data            # all instances
    python -m scripts.prepare_data --only blockseq_n544_blocks20_slots24_seed42
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INSTANCES = ROOT / "benchmark" / "raw_instances"
SOLUTIONS = ROOT / "outputs" / "solves" / "base"


def unpack_solutions(force: bool) -> None:
    for packed in sorted(SOLUTIONS.glob("*.sol.gz")):
        target = packed.with_suffix("")
        if target.exists() and not force:
            continue
        with gzip.open(packed, "rb") as fin, target.open("wb") as fout:
            shutil.copyfileobj(fin, fout)
        print(f"unpacked {target.relative_to(ROOT)}", flush=True)


def build_lp(instance_dir: Path, force: bool) -> None:
    from problems.exam_block_seq.dataloader import load_block_seq_data_from_mapping
    from problems.exam_block_seq.solver import build_exam_gurobi_model

    lp_path = instance_dir / "model.lp"
    if lp_path.exists() and lp_path.stat().st_size > 0 and not force:
        print(f"exists   {lp_path.relative_to(ROOT)}", flush=True)
        return
    start = time.time()
    # The loader only needs the LP path to exist; the model itself comes from the CSV files.
    lp_path.touch()
    try:
        data = load_block_seq_data_from_mapping({"instance_dir": str(instance_dir)})
        model = build_exam_gurobi_model(
            blocks=data["blocks"],
            slots_per_day=3,
            triple_24_start=data["triple_24_start"],
            triple_day_start=data["triple_day_start"],
            eve_morn_start=data["eve_morn_start"],
            other_b2b_start=data["other_b2b_start"],
            weights=data["weights"],
            p=data["pair_counts"],
            t=data["triplet_counts"],
            large_blocks=data["large_blocks"],
            early_slots=data["early_slots"],
        )
        model.write(str(lp_path))
    except BaseException:
        lp_path.unlink(missing_ok=True)
        raise
    print(f"built    {lp_path.relative_to(ROOT)} ({time.time() - start:.0f}s)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", default=[], help="instance directory name (repeatable)")
    parser.add_argument("--skip-lp", action="store_true", help="only unpack the base solutions")
    parser.add_argument("--force", action="store_true", help="overwrite existing artifacts")
    args = parser.parse_args()

    unpack_solutions(args.force)
    if args.skip_lp:
        return 0
    instance_dirs = sorted(p for p in INSTANCES.iterdir() if (p / "instance.json").exists())
    if args.only:
        instance_dirs = [p for p in instance_dirs if p.name in set(args.only)]
        missing = set(args.only) - {p.name for p in instance_dirs}
        if missing:
            raise SystemExit(f"unknown instances: {sorted(missing)}")
    for instance_dir in instance_dirs:
        build_lp(instance_dir, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
