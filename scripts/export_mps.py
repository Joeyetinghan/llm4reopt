#!/usr/bin/env python3
"""Export the exam benchmark as re-optimization series (MPS models + solutions).

For every instance this writes one base model and, per task, the model after the
task's gold edit, all with identical variable and constraint names and order, so
each task is a pure data change of the base model:

    mps/<I>/base.mps.gz          block-sequencing MIP of the deployed timetable
    mps/<I>/base.sol.gz          deployed schedule as a full assignment
    mps/<I>/<task>/task.mps.gz   base model with the gold edit applied
    mps/<I>/<task>/reference.sol.gz  reference schedule as a full assignment
    mps/<I>/<task>/change.json   prompt, gold edit, change classes, diff summary
    mps/<I>/<task>/diff.csv.gz   every changed coefficient (kind,name,base,task)
    mps/manifest.json            sizes, checksums and verification results

Models are built from ``benchmark/instances/<I>.json`` with the same gurobipy
builder as ``scripts/prepare_data.py`` and need ``gurobipy`` with a license for
models of this size (~0.7M binary variables). Edits are mapped as follows:

* objective (P2 pair counts, P4 weights): new objective coefficients;
* bounds (P1 reserved slot): x[i,*,*,s] fixed to 0 for every real block i, so any
  virtual block may hold slot s (virtual blocks are interchangeable);
* bounds (P3 earlier cutoff): x[i,*,*,s] fixed to 0 for large blocks i and the
  slots removed from the early-slot set;
* rhs (P5 load cap): the base carries the cap row with a non-binding right-hand
  side (total enrollment of its blocks); the task sets the cap;
* P6 composes the above.

    python -m scripts.export_mps                   # all instances and tasks
    python -m scripts.export_mps --only I5
    python -m scripts.export_mps --tasks I5_P1 I5_P3 --out /tmp/mps
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark import reopt_exam  # noqa: E402

FORMAT_VERSION = 1
REL_TOL = 1e-6


# ---------------------------------------------------------------------------
# Schedules as full assignments (no Gurobi needed)
# ---------------------------------------------------------------------------


def triple_slots(instance: Mapping[str, Any]) -> list[int]:
    starts = instance["penalty_starts"]
    return sorted(set(starts["triple_day_start"]) | set(starts["triple_24_start"]))


def schedule_assignment(instance: Mapping[str, Any], schedule: Mapping[int, int]) -> dict[str, float]:
    """Nonzero x/y/z values of a block->slot schedule in the block-sequencing MIP.

    x[i,j,k,s] = 1 when blocks i, j, k occupy slots s, s+1, s+2 (cyclic);
    y[i,j,k] = 1 when that sequence starts in a triple slot; z[i,j,k,l] takes its
    smallest feasible value max(0, y[i,j,k] + y[j,k,l] - 1).
    """
    slots = list(instance["slots"])
    n = len(slots)
    slot_to_block = {slot: block for block, slot in schedule.items()}

    def at(slot: int, offset: int) -> int:
        return slot_to_block[slots[(slots.index(slot) + offset) % n]]

    values: dict[str, float] = {}
    y_on: set[tuple[int, int, int]] = set()
    triples = set(triple_slots(instance))
    for s in slots:
        i, j, k = at(s, 0), at(s, 1), at(s, 2)
        values[f"x[{i},{j},{k},{s}]"] = 1.0
        if s in triples:
            y_on.add((i, j, k))
    for i, j, k in sorted(y_on):
        values[f"y[{i},{j},{k}]"] = 1.0
    for i, j, k in sorted(y_on):
        for a, b, l in sorted(y_on):
            if (a, b) == (j, k):
                values[f"z[{i},{j},{k},{l}]"] = 1.0
    return values


def write_solution(path: Path, names: Iterable[str], nonzeros: Mapping[str, float], objective: float) -> None:
    """Write a Gurobi-readable .sol(.gz) listing every variable."""
    buf = io.StringIO()
    buf.write(f"# Objective value = {objective!r}\n")
    for name in names:
        value = nonzeros.get(name, 0.0)
        buf.write(f"{name} {value:g}\n")
    data = buf.getvalue().encode("utf-8")
    if path.suffix == ".gz":
        with gzip.GzipFile(path, "wb", mtime=0) as fh:
            fh.write(data)
    else:
        path.write_bytes(data)


# ---------------------------------------------------------------------------
# Task edits -> model data changes
# ---------------------------------------------------------------------------


def cap_rows(instance: Mapping[str, Any], tasks: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Load-cap rows needed by any task of the instance, keyed by constraint name."""
    rows: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for cap in reopt_exam.apply_edit(instance, task["gold_edit"])["slot_load_caps"]:
            name = _cap_row_name(instance, cap)
            rows.setdefault(name, {"slots": sorted(cap["slots"]), "blocks": sorted(cap["blocks"])})
    return rows


def _cap_row_name(instance: Mapping[str, Any], cap: Mapping[str, Any]) -> str:
    suffix = "" if sorted(cap["blocks"]) == sorted(instance["real_blocks"]) else "_allblocks"
    return "slot_load_cap[s" + "_".join(str(s) for s in sorted(cap["slots"])) + suffix + "]"


def _check_virtual_blocks_interchangeable(instance: Mapping[str, Any]) -> None:
    virtual = set(instance["virtual_blocks"])
    bad = [key for key, count in instance["pair_counts"].items() if count and virtual & set(key)]
    bad += [key for key, count in instance["triplet_counts"].items() if count and virtual & set(key)]
    bad += [b for b in virtual if instance["block_enrollment"].get(b, 0.0) or b in instance["large_blocks"]]
    if bad:
        raise SystemExit(f"{instance['instance_id']}: virtual blocks are not interchangeable: {bad[:5]}")


class Exporter:
    def __init__(self, instance: dict[str, Any], tasks: list[dict[str, Any]]) -> None:
        import gurobipy as gp

        from problems.exam_block_seq.objective import refresh_exam_lp_objective
        from problems.exam_block_seq.solver import build_exam_gurobi_model

        self.gp = gp
        self._refresh = refresh_exam_lp_objective
        self.instance = instance
        self.caps = cap_rows(instance, tasks)
        inst = instance
        model = build_exam_gurobi_model(
            blocks=inst["blocks"],
            slots_per_day=inst["slots_per_day"],
            weights=inst["weights"],
            p=inst["pair_counts"],
            t=inst["triplet_counts"],
            large_blocks=inst["large_blocks"],
            early_slots=inst["early_slots"],
            **inst["penalty_starts"],
        )
        self.x = {}
        for var in model.getVars():
            if var.VarName.startswith("x["):
                i, j, k, s = (int(v) for v in var.VarName[2:-1].split(","))
                self.x[i, j, k, s] = var
        for name, row in self.caps.items():
            coeffs = [inst["block_enrollment"].get(b, 0.0) for b in row["blocks"]]
            expr = gp.quicksum(
                c * self.x[b, j, k, s]
                for b, c in zip(row["blocks"], coeffs)
                if c
                for j in inst["blocks"]
                for k in inst["blocks"]
                for s in row["slots"]
            )
            row["placeholder_rhs"] = float(sum(coeffs))
            model.addConstr(expr <= row["placeholder_rhs"], name=name)
        model.update()
        self.model = model
        self.vars = model.getVars()
        self.names = model.getAttr("VarName", self.vars)
        self.base_obj = model.getAttr("Obj", self.vars)
        self.base_ub = model.getAttr("UB", self.vars)
        self.index = {name: idx for idx, name in enumerate(self.names)}
        self.cap_constrs = {name: model.getConstrByName(name) for name in self.caps}
        # The builder and the runtime's objective refresh must agree on the base data.
        self._set_objective(inst)
        if model.getAttr("Obj", self.vars) != self.base_obj:
            raise SystemExit(f"{inst['instance_id']}: objective refresh disagrees with the model builder")

    def _set_objective(self, inst: Mapping[str, Any]) -> list[float]:
        starts = inst["penalty_starts"]
        self._refresh(
            self.model,
            weights=inst["weights"],
            pair_counts=inst["pair_counts"],
            triplet_counts=inst["triplet_counts"],
            triple_day_start=set(starts["triple_day_start"]),
            triple_24_start=set(starts["triple_24_start"]),
            eve_morn_start=set(starts["eve_morn_start"]),
            other_b2b_start=set(starts["other_b2b_start"]),
        )
        return self.model.getAttr("Obj", self.vars)

    def task_changes(self, edited: Mapping[str, Any]) -> dict[str, Any]:
        """Data changes that turn the base model into the edited instance's model."""
        base = self.instance
        changes: dict[str, Any] = {"obj": {}, "ub": {}, "rhs": {}, "notes": []}
        if (edited["weights"], edited["pair_counts"], edited["triplet_counts"]) != (
            base["weights"],
            base["pair_counts"],
            base["triplet_counts"],
        ):
            new_obj = self._set_objective(edited)
            self.model.setAttr("Obj", self.vars, self.base_obj)
            changes["obj"] = {
                idx: new for idx, (old, new) in enumerate(zip(self.base_obj, new_obj)) if old != new
            }

        if sorted(edited["large_blocks"]) != sorted(base["large_blocks"]):
            raise SystemExit("changing the set of large blocks is not supported")
        removed = sorted(set(base["early_slots"]) - set(edited["early_slots"]))
        if set(edited["early_slots"]) - set(base["early_slots"]):
            raise SystemExit("early slots must be a subset of the base early slots")
        blocks = edited["blocks"]
        if removed:
            changes["notes"].append(
                f"frontload: large blocks fixed out of slots {removed}; with each_i this equals the "
                "frontload rows over the new early-slot set (same LP relaxation)"
            )
            for i in edited["large_blocks"]:
                for s in removed:
                    for j in blocks:
                        for k in blocks:
                            changes["ub"][self.index[self.x[i, j, k, s].VarName]] = 0.0

        if edited["reserved"]:
            _check_virtual_blocks_interchangeable(edited)
            virtual = set(edited["virtual_blocks"])
            for row in edited["reserved"]:
                s = int(row["slot"])
                changes["notes"].append(
                    f"reserved slot {s}: real blocks fixed out of slot {s}, so a virtual block holds it "
                    f"(virtual blocks are interchangeable; gold edit names block {row['block']})"
                )
                for i in blocks:
                    if i in virtual:
                        continue
                    for j in blocks:
                        for k in blocks:
                            changes["ub"][self.index[self.x[i, j, k, s].VarName]] = 0.0

        for cap in edited["slot_load_caps"]:
            name = _cap_row_name(edited, cap)
            changes["rhs"][name] = float(cap["cap"])

        changes["ub"] = {idx: v for idx, v in changes["ub"].items() if self.base_ub[idx] != v}
        changes["rhs"] = {n: v for n, v in changes["rhs"].items() if self.caps[n]["placeholder_rhs"] != v}
        return changes

    def apply(self, changes: Mapping[str, Any], *, revert: bool = False) -> None:
        if changes["obj"]:
            idxs = list(changes["obj"])
            values = [self.base_obj[i] for i in idxs] if revert else [changes["obj"][i] for i in idxs]
            self.model.setAttr("Obj", [self.vars[i] for i in idxs], values)
        if changes["ub"]:
            idxs = list(changes["ub"])
            values = [self.base_ub[i] for i in idxs] if revert else [changes["ub"][i] for i in idxs]
            self.model.setAttr("UB", [self.vars[i] for i in idxs], values)
        for name, rhs in changes["rhs"].items():
            self.cap_constrs[name].RHS = self.caps[name]["placeholder_rhs"] if revert else rhs
        self.model.update()

    def objective_of(self, nonzeros: Mapping[str, float], changes: Mapping[str, Any] | None = None) -> float:
        obj = dict(changes["obj"]) if changes else {}
        total = 0.0
        for name, value in nonzeros.items():
            idx = self.index[name]
            total += obj.get(idx, self.base_obj[idx]) * value
        return total

    def diff_rows(self, changes: Mapping[str, Any]) -> list[tuple[str, str, float, float]]:
        rows = [("obj", self.names[i], self.base_obj[i], v) for i, v in sorted(changes["obj"].items())]
        rows += [("ub", self.names[i], self.base_ub[i], v) for i, v in sorted(changes["ub"].items())]
        rows += [("rhs", n, self.caps[n]["placeholder_rhs"], v) for n, v in sorted(changes["rhs"].items())]
        return rows


def change_classes(changes: Mapping[str, Any]) -> list[str]:
    classes = []
    if changes["obj"]:
        classes.append("objective")
    if changes["ub"]:
        classes.append("bounds")
    if changes["rhs"]:
        classes.append("rhs")
    return classes


def write_diff(path: Path, rows: list[tuple[str, str, float, float]]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["kind", "name", "base", "task"])
    for kind, name, old, new in rows:
        writer.writerow([kind, name, repr(float(old)), repr(float(new))])
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(buf.getvalue().encode("utf-8"))


# ---------------------------------------------------------------------------
# Verification of the written files
# ---------------------------------------------------------------------------


class Verifier:
    def __init__(self) -> None:
        import gurobipy as gp

        self.gp = gp
        self.env = gp.Env(params={"OutputFlag": 0})

    def read(self, path: Path):
        model = self.gp.read(str(path), self.env)
        model.Params.OutputFlag = 0
        return model

    def signature(self, model) -> tuple[list[str], list[str]]:
        return model.getAttr("VarName", model.getVars()), model.getAttr("ConstrName", model.getConstrs())

    def check_solution(self, model, sol_path: Path) -> tuple[bool, float | None]:
        """Fix every variable to the .sol value and report (feasible, objective)."""
        variables = model.getVars()
        lb, ub = model.getAttr("LB", variables), model.getAttr("UB", variables)
        model.read(str(sol_path))
        model.update()
        start = model.getAttr("Start", variables)
        if any(not (lo - 1e-9 <= value <= hi + 1e-9) for lo, value, hi in zip(lb, start, ub)):
            return False, None
        model.setAttr("LB", variables, start)
        model.setAttr("UB", variables, start)
        model.Params.DualReductions = 0
        model.optimize()
        feasible = model.Status == self.gp.GRB.OPTIMAL
        objective = model.ObjVal if feasible else None
        model.setAttr("LB", variables, lb)
        model.setAttr("UB", variables, ub)
        model.reset()
        model.update()
        return feasible, objective


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= REL_TOL * max(1.0, abs(a), abs(b))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def export_instance(
    instance_id: str,
    tasks: list[dict[str, Any]],
    all_tasks: list[dict[str, Any]],
    out_dir: Path,
    *,
    verify: bool,
) -> dict[str, Any]:
    start = time.time()
    instance = reopt_exam.load_instance(instance_id)
    exporter = Exporter(instance, all_tasks)
    inst_dir = out_dir / instance_id
    inst_dir.mkdir(parents=True, exist_ok=True)

    base_schedule = reopt_exam.parse_schedule(instance["base_solution"]["schedule"])
    base_nonzeros = schedule_assignment(instance, base_schedule)
    base_obj = exporter.objective_of(base_nonzeros)
    exporter.model.write(str(inst_dir / "base.mps.gz"))
    write_solution(inst_dir / "base.sol.gz", exporter.names, base_nonzeros, base_obj)
    base_eval = reopt_exam.evaluate(instance, base_schedule)
    record: dict[str, Any] = {
        "instance_id": instance_id,
        "name": instance["name"],
        "num_vars": exporter.model.NumVars,
        "num_constrs": exporter.model.NumConstrs,
        "num_nonzeros": exporter.model.NumNZs,
        "cap_rows": {n: dict(r) for n, r in exporter.caps.items()},
        "base": {
            "objective": base_obj,
            "evaluator_objective": base_eval["objective"],
            "reported_objective": instance["base_solution"]["objective"],
        },
        "tasks": {},
    }
    print(f"{instance_id}: base written ({time.time() - start:.0f}s)", flush=True)

    for task in tasks:
        task_dir = inst_dir / task["task_id"]
        task_dir.mkdir(exist_ok=True)
        edited = reopt_exam.apply_edit(instance, task["gold_edit"])
        changes = exporter.task_changes(edited)
        ref_schedule = reopt_exam.parse_schedule(task["reference"]["schedule"])
        ref_nonzeros = schedule_assignment(instance, ref_schedule)
        ref_obj = exporter.objective_of(ref_nonzeros, changes)
        ref_eval = reopt_exam.evaluate_task(task, ref_schedule)
        base_in_task = reopt_exam.evaluate(instance, base_schedule, task["gold_edit"])

        exporter.apply(changes)
        exporter.model.write(str(task_dir / "task.mps.gz"))
        exporter.apply(changes, revert=True)
        write_solution(task_dir / "reference.sol.gz", exporter.names, ref_nonzeros, ref_obj)
        diff = exporter.diff_rows(changes)
        write_diff(task_dir / "diff.csv.gz", diff)
        change = {
            "format_version": FORMAT_VERSION,
            "task_id": task["task_id"],
            "instance_id": instance_id,
            "prompt_id": task["prompt_id"],
            "prompt": task["prompt"],
            "gold_edit": task["gold_edit"],
            "change_classes": change_classes(changes),
            "diff_summary": {
                "objective_coefficients": len(changes["obj"]),
                "bounds": len(changes["ub"]),
                "rhs": len(changes["rhs"]),
                "matrix_coefficients": 0,
                "rows_added": 0,
                "cols_added": 0,
            },
            "notes": changes["notes"],
            "reference": {
                "objective": ref_obj,
                "reported_objective": task["reference"]["objective"],
                "obj_bound": task["reference"].get("obj_bound"),
                "status": task["reference"].get("status"),
            },
            "base_solution_feasible": not base_in_task["violations"],
            "files": {
                "base_model": "../base.mps.gz",
                "base_solution": "../base.sol.gz",
                "model": "task.mps.gz",
                "reference_solution": "reference.sol.gz",
                "diff": "diff.csv.gz",
            },
        }
        (task_dir / "change.json").write_text(json.dumps(change, indent=2) + "\n", encoding="utf-8")
        record["tasks"][task["task_id"]] = {
            "change_classes": change["change_classes"],
            "diff_summary": change["diff_summary"],
            "reference_objective": ref_obj,
            "evaluator_objective": ref_eval["objective"],
            "evaluator_feasible": ref_eval["feasible"],
            "base_solution_feasible": change["base_solution_feasible"],
        }
        print(f"{task['task_id']}: {'+'.join(change['change_classes'])} ({time.time() - start:.0f}s)", flush=True)

    if verify:
        record["verification"] = verify_instance(inst_dir, record)
    for path in sorted(inst_dir.rglob("*")):
        if path.is_file():
            record.setdefault("files", {})[str(path.relative_to(out_dir))] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    record["seconds"] = round(time.time() - start, 1)
    print(f"{instance_id}: done ({record['seconds']:.0f}s)", flush=True)
    return record


def verify_instance(inst_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Re-read the written files and check names, solutions and objectives."""
    verifier = Verifier()
    base = verifier.read(inst_dir / "base.mps.gz")
    base_sig = verifier.signature(base)
    feasible, objective = verifier.check_solution(base, inst_dir / "base.sol.gz")
    result: dict[str, Any] = {
        "base": {
            "solution_feasible": feasible,
            "objective": objective,
            "objective_matches_evaluator": objective is not None
            and _close(objective, record["base"]["evaluator_objective"]),
        },
        "tasks": {},
    }
    base.dispose()
    for task_id, info in record["tasks"].items():
        model = verifier.read(inst_dir / task_id / "task.mps.gz")
        same_dims = verifier.signature(model) == base_sig
        ref_feasible, ref_obj = verifier.check_solution(model, inst_dir / task_id / "reference.sol.gz")
        base_feasible, _ = verifier.check_solution(model, inst_dir / "base.sol.gz")
        model.dispose()
        result["tasks"][task_id] = {
            "same_names_and_order": same_dims,
            "reference_feasible": ref_feasible,
            "reference_objective": ref_obj,
            "objective_matches_evaluator": ref_obj is not None and _close(ref_obj, info["evaluator_objective"]),
            "base_solution_feasible": base_feasible,
            "base_feasibility_matches_evaluator": base_feasible == info["base_solution_feasible"],
        }
    checks = [result["base"]["solution_feasible"], result["base"]["objective_matches_evaluator"]]
    for row in result["tasks"].values():
        checks += [
            row["same_names_and_order"],
            row["reference_feasible"],
            row["objective_matches_evaluator"],
            row["base_feasibility_matches_evaluator"],
        ]
    result["ok"] = all(checks)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", default=[], help="instance id such as I5 (repeatable)")
    parser.add_argument("--tasks", nargs="+", default=[], help="task ids such as I5_P1 (default: all)")
    parser.add_argument("--out", type=Path, default=ROOT / "mps", help="output directory (default: mps/)")
    parser.add_argument("--no-verify", action="store_true", help="skip re-reading the written files")
    args = parser.parse_args(argv)

    all_tasks = list(reopt_exam.load_tasks().values())
    known = {t["task_id"] for t in all_tasks}
    if set(args.tasks) - known:
        raise SystemExit(f"unknown tasks: {sorted(set(args.tasks) - known)}")
    instance_ids = sorted({t["instance_id"] for t in all_tasks})
    if set(args.only) - set(instance_ids):
        raise SystemExit(f"unknown instances: {sorted(set(args.only) - set(instance_ids))}")
    if args.only:
        instance_ids = [i for i in instance_ids if i in set(args.only)]
    if args.tasks:
        instance_ids = [i for i in instance_ids if any(t.startswith(f"{i}_") for t in args.tasks)]

    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    manifest: dict[str, Any] = {"format_version": FORMAT_VERSION, "instances": {}}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import gurobipy as gp

    manifest["gurobi_version"] = ".".join(str(v) for v in gp.gurobi.version())
    ok = True
    for instance_id in instance_ids:
        inst_tasks = [t for t in all_tasks if t["instance_id"] == instance_id]
        selected = [t for t in inst_tasks if not args.tasks or t["task_id"] in set(args.tasks)]
        record = export_instance(instance_id, selected, inst_tasks, args.out, verify=not args.no_verify)
        manifest["instances"][instance_id] = record
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if "verification" in record:
            ok &= record["verification"]["ok"]
            _print_verification(record)
    return 0 if ok else 1


def _print_verification(record: Mapping[str, Any]) -> None:
    ver = record["verification"]
    print(f"{record['instance_id']}: base.sol feasible={ver['base']['solution_feasible']} "
          f"objective={ver['base']['objective']}")
    for task_id, row in ver["tasks"].items():
        print(
            f"  {task_id}: names={row['same_names_and_order']} ref_feasible={row['reference_feasible']} "
            f"obj={row['reference_objective']} obj_ok={row['objective_matches_evaluator']} "
            f"base_feasible={row['base_solution_feasible']} base_ok={row['base_feasibility_matches_evaluator']}"
        )
    print(f"{record['instance_id']}: verification {'ok' if ver['ok'] else 'FAILED'}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
