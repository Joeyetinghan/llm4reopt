"""Dependency-free evaluator for the Cornell exam re-optimization benchmark.

A schedule maps every block id (real and virtual) to a distinct slot id. This
module scores a schedule against an instance, optionally after applying one of
the benchmark's structured edits (the "gold edit" of a task), without Gurobi.

    python benchmark/reopt_exam.py --task I1_P3 --schedule my_schedule.json
    python benchmark/reopt_exam.py --instance I1 --schedule my_schedule.json

`objective()` reproduces the block-sequencing MIP objective exactly, and
`paper_metrics()` reproduces the schedule-quality columns reported in the paper
(computed on the unedited instance counts, as in the paper).
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

BENCHMARK_DIR = Path(__file__).resolve().parent
INSTANCES_DIR = BENCHMARK_DIR / "instances"
TASKS_PATH = BENCHMARK_DIR / "tasks.jsonl"

Schedule = dict[int, int]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_instance(instance: str | Path) -> dict[str, Any]:
    """Load an instance by id (``"I1"``) or by path to its JSON file."""
    path = Path(instance)
    if not path.suffix:
        path = INSTANCES_DIR / f"{instance}.json"
    with path.open("r", encoding="utf-8") as fh:
        return _index_instance(json.load(fh))


def load_tasks(path: str | Path = TASKS_PATH) -> dict[str, dict[str, Any]]:
    """Return tasks keyed by task id (``"I1_P3"``)."""
    tasks: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                task = json.loads(line)
                tasks[task["task_id"]] = task
    return tasks


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key {key!r}")
        obj[key] = value
    return obj


def loads_strict(text: str) -> Any:
    """``json.loads`` that raises ``ValueError`` on duplicate object keys."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def parse_schedule(value: Any) -> Schedule:
    """Accept ``{"block": slot}`` mappings (str or int keys) or a JSON string.

    Block ids and slots must be integers: ``"3"``, ``3`` and ``3.0`` are accepted,
    ``3.9``, ``True`` and ``"03"``-style duplicates of another key raise ``ValueError``.
    """
    if isinstance(value, str):
        value = loads_strict(value)
    if not isinstance(value, Mapping):
        raise TypeError("schedule must be a mapping of block -> slot")
    schedule: Schedule = {}
    for raw_block, raw_slot in value.items():
        block = _as_int(raw_block, "block id")
        if block in schedule:
            raise ValueError(f"block {block} appears more than once (key {raw_block!r})")
        schedule[block] = _as_int(raw_slot, f"slot of block {block}")
    return schedule


def _as_int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ValueError(f"{what} must be an integer, got {value!r}")


def _index_instance(raw: dict[str, Any]) -> dict[str, Any]:
    inst = dict(raw)
    inst["pair_counts"] = {(int(i), int(j)): float(c) for i, j, c in raw["pair_counts"]}
    inst["triplet_counts"] = {(int(i), int(j), int(k)): float(c) for i, j, k, c in raw["triplet_counts"]}
    inst["block_enrollment"] = {int(b): float(v) for b, v in raw["block_enrollment"].items()}
    inst.setdefault("reserved", [])
    inst.setdefault("slot_load_caps", [])
    return inst


# ---------------------------------------------------------------------------
# Edits (the structured form of each prompt)
# ---------------------------------------------------------------------------


def apply_edit(instance: dict[str, Any], edit: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``instance`` with a structured edit applied.

    Supported ops:
      reserve_slot_for_virtual_block  {"slot"}  (any virtual block, i.e. the slot stays empty;
                                      an optional int "block" pins one specific virtual block)
      increase_pair_count             {"pairs": [[i, j], ...], "delta"}
      set_early_slots                 {"early_slots": [...]}  (frontload rows for large blocks)
      set_weights                     {"weights": {"gamma1": ..., "beta": ...}}
      slot_load_cap                   {"slots": [...], "cap", "blocks": "real"}
      sequence                        {"edits": [edit, ...]}  (applied in order)
    """
    out = copy.deepcopy(instance)
    if edit:
        _apply_edit_in_place(out, edit)
    return out


def _apply_edit_in_place(inst: dict[str, Any], edit: Mapping[str, Any]) -> None:
    op = edit["op"]
    if op == "sequence":
        for sub in edit["edits"]:
            _apply_edit_in_place(inst, sub)
    elif op == "reserve_slot_for_virtual_block":
        block = edit.get("block")
        inst["reserved"].append({"slot": int(edit["slot"]), "block": int(block) if isinstance(block, int) and not isinstance(block, bool) else None})
    elif op == "increase_pair_count":
        for i, j in edit["pairs"]:
            key = (int(i), int(j))
            inst["pair_counts"][key] = inst["pair_counts"].get(key, 0.0) + float(edit["delta"])
    elif op == "set_early_slots":
        inst["early_slots"] = [int(s) for s in edit["early_slots"]]
    elif op == "set_weights":
        inst["weights"] = {**inst["weights"], **{k: float(v) for k, v in edit["weights"].items()}}
    elif op == "slot_load_cap":
        blocks = inst["real_blocks"] if edit.get("blocks", "real") == "real" else inst["blocks"]
        inst["slot_load_caps"].append(
            {"slots": [int(s) for s in edit["slots"]], "cap": float(edit["cap"]), "blocks": list(blocks)}
        )
    else:
        raise ValueError(f"Unknown edit op: {op}")


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------


def check_schedule(instance: dict[str, Any], schedule: Schedule) -> list[str]:
    """Return human-readable constraint violations (empty list means feasible)."""
    violations: list[str] = []
    blocks = set(instance["blocks"])
    slots = set(instance["slots"])
    if set(schedule) != blocks:
        missing = sorted(blocks - set(schedule))
        extra = sorted(set(schedule) - blocks)
        violations.append(f"block_assignment: missing={missing} extra={extra}")
    used = list(schedule.values())
    if set(used) - slots:
        violations.append(f"slot_assignment: unknown slots {sorted(set(used) - slots)}")
    duplicates = sorted({s for s in used if used.count(s) > 1})
    if duplicates:
        violations.append(f"slot_assignment: slots used more than once {duplicates}")

    early = set(instance["early_slots"])
    for block in instance["large_blocks"]:
        slot = schedule.get(block)
        if slot not in early:
            violations.append(f"frontload: large block {block} in slot {slot}, allowed {sorted(early)}")

    virtual = set(instance["virtual_blocks"])
    slot_to_block = {slot: block for block, slot in schedule.items()}
    for row in instance["reserved"]:
        held = slot_to_block.get(row["slot"])
        if row["block"] is not None and held != row["block"]:
            violations.append(f"reserved_slot: slot {row['slot']} must hold virtual block {row['block']}, holds {held}")
        elif row["block"] is None and held not in virtual:
            violations.append(f"reserved_slot: slot {row['slot']} must stay empty (hold a virtual block), holds block {held}")

    for cap in instance["slot_load_caps"]:
        load = slot_load(instance, schedule, cap["slots"], cap["blocks"])
        if load > cap["cap"] + 1e-9:
            violations.append(f"slot_load_cap: slots {cap['slots']} load {load:g} > cap {cap['cap']:g}")
    return violations


def slot_load(
    instance: dict[str, Any], schedule: Schedule, slots: Iterable[int], blocks: Iterable[int] | None = None
) -> float:
    allowed = set(instance["real_blocks"] if blocks is None else blocks)
    wanted = set(slots)
    return sum(
        instance["block_enrollment"].get(block, 0.0)
        for block, slot in schedule.items()
        if slot in wanted and block in allowed
    )


# ---------------------------------------------------------------------------
# Objective (exactly the block-sequencing MIP objective)
# ---------------------------------------------------------------------------


def objective(instance: dict[str, Any], schedule: Schedule) -> float:
    """MIP objective of a full schedule.

    gamma1 * evening->morning back-to-backs + gamma2 * other back-to-backs
    + alpha * three-in-a-day + beta * three-in-24h + delta * three-in-four-slots,
    using ordered pair/triplet counts exactly as in the LP.
    """
    p, t, w = instance["pair_counts"], instance["triplet_counts"], instance["weights"]
    starts = instance["penalty_starts"]
    n = len(instance["slots"])
    slot_to_block = {slot: block for block, slot in schedule.items()}

    def at(slot: int, offset: int) -> int:
        return slot_to_block[((slot - 1 + offset) % n) + 1]

    total = 0.0
    for s in starts["eve_morn_start"]:
        total += w["gamma1"] * p.get((at(s, 0), at(s, 1)), 0.0)
    for s in starts["other_b2b_start"]:
        total += w["gamma2"] * p.get((at(s, 0), at(s, 1)), 0.0)
    for s in starts["triple_day_start"]:
        total += w["alpha"] * t.get((at(s, 0), at(s, 1), at(s, 2)), 0.0)
    for s in starts["triple_24_start"]:
        total += w["beta"] * t.get((at(s, 0), at(s, 1), at(s, 2)), 0.0)
    triple_starts = set(starts["triple_day_start"]) | set(starts["triple_24_start"])
    for s in instance["slots"]:
        if s in triple_starts and ((s % n) + 1) in triple_starts:
            i, j, k, l = (at(s, q) for q in range(4))
            total += w["delta"] * (t.get((i, j, k), 0.0) + t.get((i, k, l), 0.0))
    return total


# ---------------------------------------------------------------------------
# Paper schedule-quality metrics
# ---------------------------------------------------------------------------


def paper_metrics(instance: dict[str, Any], schedule: Schedule) -> dict[str, float]:
    """Student-facing counts reported in the paper (non-cyclic windows)."""
    slot_to_block = {slot: block for block, slot in schedule.items()}
    max_slot = max(instance["slots"])
    starts = instance["penalty_starts"]

    def window(start: int, width: int) -> tuple[int, ...] | None:
        span = range(start, start + width)
        if span[-1] > max_slot or any(s not in slot_to_block for s in span):
            return None
        return tuple(slot_to_block[s] for s in span)

    triple = 0.0
    for s in sorted(set(starts["triple_day_start"]) | set(starts["triple_24_start"])):
        blocks = window(s, 3)
        if blocks is not None:
            triple += _triplet(instance, blocks)

    b2b = 0.0
    for s in sorted(set(starts["eve_morn_start"]) | set(starts["other_b2b_start"])):
        blocks = window(s, 2)
        if blocks is not None:
            b2b += _pair(instance, *blocks)

    two_in_24 = 0.0
    for a in sorted(slot_to_block):
        for distance in range(1, int(instance["slots_per_day"]) + 1):
            b = a + distance
            if b <= max_slot and b in slot_to_block:
                two_in_24 += _pair(instance, slot_to_block[a], slot_to_block[b])

    three_in_4 = 0.0
    for start in range(1, max_slot - 2):
        blocks = window(start, 4)
        if blocks is None:
            continue
        for idxs in itertools.combinations(range(4), 3):
            three_in_4 += _triplet(instance, tuple(blocks[i] for i in idxs))

    return {
        "triple_count": triple,
        "back_to_back_count": b2b,
        "two_in_24hr_count": two_in_24,
        "three_in_4_slots_count": three_in_4,
    }


def _pair(instance: dict[str, Any], a: int, b: int) -> float:
    p = instance["pair_counts"]
    return p.get((a, b), p.get((b, a), 0.0))


def _triplet(instance: dict[str, Any], blocks: tuple[int, ...]) -> float:
    t = instance["triplet_counts"]
    if blocks in t:
        return t[blocks]
    for perm in itertools.permutations(blocks):
        if perm in t:
            return t[perm]
    return 0.0


# ---------------------------------------------------------------------------
# One-call evaluation
# ---------------------------------------------------------------------------


def evaluate(
    instance: dict[str, Any],
    schedule: Mapping[Any, Any] | str,
    edit: Mapping[str, Any] | None = None,
    reference_objective: float | None = None,
) -> dict[str, Any]:
    """Score a schedule for an instance, optionally under a structured edit.

    Feasibility and the objective use the edited instance; ``paper_metrics``
    use the unedited instance counts so they are comparable across prompts.
    """
    sched = parse_schedule(schedule)
    edited = apply_edit(instance, edit)
    violations = check_schedule(edited, sched)
    result: dict[str, Any] = {
        "feasible": not violations,
        "violations": violations,
        "objective": None,
        "metrics": None,
    }
    if not any(v.startswith(("block_assignment", "slot_assignment")) for v in violations):
        result["objective"] = objective(edited, sched)
        result["metrics"] = paper_metrics(instance, sched)
    if reference_objective is not None and result["objective"] is not None:
        result["reference_objective"] = float(reference_objective)
        result["gap_to_reference_pct"] = 100.0 * (result["objective"] - reference_objective) / reference_objective
    return result


def evaluate_task(task: Mapping[str, Any], schedule: Mapping[Any, Any] | str) -> dict[str, Any]:
    """Score a schedule against a benchmark task (gold edit + reference objective)."""
    instance = load_instance(task["instance_id"])
    return evaluate(instance, schedule, task["gold_edit"], task["reference"]["objective"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--task", help="task id such as I1_P3 (applies its gold edit)")
    target.add_argument("--instance", help="instance id such as I1 (no edit)")
    parser.add_argument(
        "--schedule",
        required=True,
        help="JSON file with a block -> slot mapping, or 'base' (the deployed schedule) "
        "or 'reference' (the task's 1-hour reference schedule, with --task only)",
    )
    args = parser.parse_args(argv)
    if args.task and args.task not in load_tasks():
        parser.error(f"unknown task {args.task!r} (expected I1_P1 ... I5_P6)")
    if args.instance and not (INSTANCES_DIR / f"{args.instance}.json").exists():
        parser.error(f"unknown instance {args.instance!r} (expected I1 ... I5)")

    if args.schedule == "base":
        instance_id = load_tasks()[args.task]["instance_id"] if args.task else args.instance
        schedule = load_instance(instance_id)["base_solution"]["schedule"]
    elif args.schedule == "reference":
        if not args.task:
            parser.error("--schedule reference requires --task")
        schedule = load_tasks()[args.task]["reference"]["schedule"]
    else:
        try:
            schedule = loads_strict(Path(args.schedule).read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"cannot read schedule file: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:  # includes json.JSONDecodeError and duplicate keys
            print(f"invalid schedule: {exc}", file=sys.stderr)
            return 2
        if isinstance(schedule, Mapping) and "schedule" in schedule:
            schedule = schedule["schedule"]
    try:
        if args.task:
            result = evaluate_task(load_tasks()[args.task], schedule)
        else:
            result = evaluate(load_instance(args.instance), schedule)
    except (TypeError, ValueError) as exc:
        print(f"invalid schedule: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["feasible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
