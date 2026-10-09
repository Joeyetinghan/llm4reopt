"""Gold edits of the six change requests, applied to an instance's ``model.lp``.

``build_reference(instance_id, prompt_id)`` loads the instance, applies the edit of the prompt
(``apply_p1`` .. ``apply_p6``) and returns a namespace holding the edited ``gurobipy`` model ``m``
with its task text, change record and assumptions; the ground-truth evaluator and
``scripts/exam_block_seq/benchmark_ground_truth.py`` solve ``m`` for the reference schedules. How each request was interpreted is listed in ``PROMPT_ASSUMPTIONS`` and explained in
``benchmark/README.md`` (Tasks).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gurobipy as gp

from problems.exam_block_seq.constraint_families import (
    apply_exam_x_aggregate_families,
    build_frontload_constraint_family,
    build_reserved_virtual_slot_constraint_family,
    build_slot_load_cap_constraint_family,
)
from problems.exam_block_seq.dataloader import load_block_seq_data_from_mapping, parse_lp_instance
from problems.exam_block_seq.objective import refresh_exam_lp_objective_from_state
from problems.exam_block_seq.prompt_params import exam_prompt_params
from problems.exam_block_seq.prompts import default_prompt_params, resolve_prompt


PROMPT_ASSUMPTIONS = {
    "P1": [
        "Treat P1 as reserving the evening slot immediately before the final evening slot for the smallest virtual block id."
    ],
    "P2": ["Apply +120 symmetrically to both ordered pair counts (4,9) and (9,4)."],
    "P3": ["Treat the slot cutoff in the prompt literally: set early_slots to the slots strictly before that cutoff and regenerate frontload rows."],
    "P4": ["Set gamma1 to the current gamma2 and beta to 20 * gamma2_base."],
    "P5": ["Use block_enrollment with x[i,*,*,s] as the slot-assignment view and add slot_load_cap for day-2 slots 4,5,6."],
    "P6": ["Apply prompts in fixed order: P4 -> P2 -> P1."],
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTANCES_ROOT = _REPO_ROOT / "benchmark" / "raw_instances"


def load_reference_instance(instance_id: str) -> tuple[gp.Model, dict[str, Any]]:
    instance_dir = _find_instance_dir(instance_id)
    data = load_block_seq_data_from_mapping({"instance_dir": str(instance_dir)})
    model = gp.read(str(instance_dir / "model.lp"))
    model.Params.OutputFlag = 0
    virtual_blocks = {int(block) for block in data.get("virtual_blocks", data.get("dummy_blocks", []))}
    state = {
        "instance_dir": instance_dir,
        "blocks": [int(block) for block in data["blocks"]],
        "slots_per_day": int(data.get("slots_per_day", 3) or 3),
        "slot_times": [str(label).strip().lower() for label in data.get("slot_times", [])],
        "virtual_blocks": sorted(virtual_blocks),
        "real_blocks": [int(block) for block in data["blocks"] if int(block) not in virtual_blocks],
        "reserved_slots": [int(slot) for slot in data.get("reserved_slots", [])],
        "large_blocks": [int(block) for block in data.get("large_blocks", [])],
        "early_slots": [int(slot) for slot in data.get("early_slots", [])],
        "weights": _weight_view(data),
        "base_weights": _weight_view(data),
        "pair_counts": {tuple(map(int, key)): float(value) for key, value in dict(data.get("pair_counts", {})).items()},
        "triplet_counts": {
            tuple(map(int, key)): float(value) for key, value in dict(data.get("triplet_counts", {})).items()
        },
        "block_enrollment": {int(key): float(value) for key, value in dict(data.get("block_enrollment", {})).items()},
        "triple_day_start": {int(slot) for slot in data.get("triple_day_start", [])},
        "triple_24_start": {int(slot) for slot in data.get("triple_24_start", [])},
        "eve_morn_start": {int(slot) for slot in data.get("eve_morn_start", [])},
        "other_b2b_start": {int(slot) for slot in data.get("other_b2b_start", [])},
    }
    return model, state


# Short task lines recorded as ``prompt_task`` in the reference solves; P3 uses the catalog text,
# whose slot cutoff depends on the instance.
_PROMPT_TASK = {
    "P1": "Reserve the penultimate evening slot for a virtual block.",
    "P2": "Increase pair counts for ordered pairs (4,9) and (9,4) by 120.",
    "P4": "Set gamma1 = gamma2 and beta = 20 * gamma2_base.",
    "P5": "Add a day-2 enrollment cap of 4000 across slots 4,5,6.",
    "P6": "Apply P4, then P2, then P1.",
}


def build_reference(instance_id: str, prompt_id: str) -> SimpleNamespace:
    """Return the instance's model with the gold edit of ``prompt_id`` applied, as ``m``."""
    apply = _APPLY.get(prompt_id)
    if apply is None:
        raise KeyError(f"No reference edit for prompt {prompt_id}")
    m, state = load_reference_instance(instance_id)
    prompt_task = _PROMPT_TASK.get(prompt_id) or resolve_prompt(
        prompt_id, params=default_prompt_params(prompt_id, context=state)
    ).text
    changes = apply(m, state)
    return SimpleNamespace(
        INSTANCE_ID=instance_id,
        PROMPT_ID=prompt_id,
        PROMPT_TASK=prompt_task,
        REFERENCE_CHANGES=changes if isinstance(changes, list) else [changes],
        ASSUMPTIONS=list(PROMPT_ASSUMPTIONS[prompt_id]),
        m=m,
        state=state,
    )


def apply_p1(model: gp.Model, state: dict[str, Any]) -> dict[str, Any]:
    reserved_slot = penultimate_evening_slot(state)
    state["reserved_slots"] = [reserved_slot]
    family = build_reserved_virtual_slot_constraint_family(
        virtual_blocks=state["virtual_blocks"],
        reserved_slots=[reserved_slot],
    )
    _apply_exam_family(model, state, family)
    virtual_block_raw = (family.lhs_spec.get("rows") or {}).get(reserved_slot, {}).get("fixed_block")
    virtual_block = int(virtual_block_raw) if isinstance(virtual_block_raw, (int, float)) else None
    return {
        "prompt_id": "P1",
        "summary": "Reserve the penultimate evening slot for a virtual block.",
        "details": {
            "slot": reserved_slot,
            "virtual_block": virtual_block,
            "num_real_blocks": len(state["real_blocks"]),
        },
    }


def apply_p2(model: gp.Model, state: dict[str, Any]) -> dict[str, Any]:
    for pair in ((4, 9), (9, 4)):
        state["pair_counts"][pair] = float(state["pair_counts"].get(pair, 0.0)) + 120.0
    refresh_objective(model, state)
    return {
        "prompt_id": "P2",
        "summary": "Increase pair counts for (4,9) and (9,4) by 120.",
        "details": {"pairs": [(4, 9), (9, 4)], "delta": 120},
    }


def apply_p3(model: gp.Model, state: dict[str, Any]) -> dict[str, Any]:
    prompt_params = exam_prompt_params("P3", context=state)
    slot_cutoff_exclusive = int(prompt_params["slot_cutoff_exclusive"])
    early_slots = [slot for slot in state["blocks"] if int(slot) < slot_cutoff_exclusive]
    state["early_slots"] = early_slots
    late_slots = [slot for slot in state["blocks"] if slot not in set(early_slots)]
    family = build_frontload_constraint_family(
        large_blocks=state["large_blocks"],
        early_slots=early_slots,
    )
    _apply_exam_family(model, state, family)
    return {
        "prompt_id": "P3",
        "summary": f"Set early_slots to slots before {slot_cutoff_exclusive} and regenerate frontload rows for large blocks.",
        "details": {
            "early_slots": early_slots,
            "late_slots": late_slots,
            "slot_cutoff_exclusive": slot_cutoff_exclusive,
            "num_large_blocks": len(state["large_blocks"]),
        },
    }


def apply_p4(model: gp.Model, state: dict[str, Any]) -> dict[str, Any]:
    gamma2_base = float(state["base_weights"]["gamma2"])
    state["weights"]["gamma1"] = float(state["weights"]["gamma2"])
    state["weights"]["beta"] = 20.0 * gamma2_base
    refresh_objective(model, state)
    return {
        "prompt_id": "P4",
        "summary": "Set gamma1 = gamma2 and beta = 20 * gamma2_base.",
        "details": {
            "gamma1": state["weights"]["gamma1"],
            "gamma2": state["weights"]["gamma2"],
            "beta": state["weights"]["beta"],
            "gamma2_base": gamma2_base,
        },
    }


def apply_p5(model: gp.Model, state: dict[str, Any]) -> dict[str, Any]:
    family = build_slot_load_cap_constraint_family(
        row_name="day_2",
        slots=[4, 5, 6],
        real_blocks=state["real_blocks"],
        block_enrollment=state["block_enrollment"],
        cap=4000.0,
    )
    _apply_exam_family(model, state, family)
    return {
        "prompt_id": "P5",
        "summary": "Add a day-2 enrollment cap of 4000 across slots 4,5,6.",
        "details": {"slots": [4, 5, 6], "cap": 4000, "row_index": "day_2"},
    }


def apply_p6(model: gp.Model, state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        apply_p4(model, state),
        apply_p2(model, state),
        apply_p1(model, state),
    ]


def penultimate_evening_slot(state: dict[str, Any]) -> int:
    slots = sorted(int(slot) for slot in state.get("blocks", []))
    slots_per_day = int(state.get("slots_per_day", 0) or 0)
    if not slots or slots_per_day <= 0:
        raise RuntimeError("Instance is missing calendar metadata for P1.")
    slot_times = [str(label).strip().lower() for label in state.get("slot_times", [])]
    evening_index = slots_per_day - 1
    if slot_times:
        evening_index = len(slot_times) - 1
    evening_slots = [slot for slot in slots if (slot - 1) % slots_per_day == evening_index]
    if len(evening_slots) >= 2:
        return evening_slots[-2]
    if evening_slots:
        return evening_slots[-1]
    raise RuntimeError("Instance is missing evening-slot metadata for P1.")


_APPLY = {"P1": apply_p1, "P2": apply_p2, "P3": apply_p3, "P4": apply_p4, "P5": apply_p5, "P6": apply_p6}


def refresh_objective(model: gp.Model, state: dict[str, Any]) -> None:
    refresh_exam_lp_objective_from_state(model, state)


def _apply_exam_family(model: gp.Model, state: dict[str, Any], family) -> None:
    semantic_families = state.setdefault("_semantic_families", {})
    semantic_families[family.name] = family.copy()
    apply_exam_x_aggregate_families(
        model,
        blocks=state["blocks"],
        families=[family],
    )


def _find_instance_dir(instance_id: str) -> Path:
    for candidate in sorted(_INSTANCES_ROOT.iterdir()):
        if not candidate.is_dir():
            continue
        lp_path = candidate / "model.lp"
        if not lp_path.exists():
            continue
        parsed = parse_lp_instance(lp_path)
        if str(parsed["instance_id"]) == instance_id:
            return candidate.resolve()
    raise FileNotFoundError(
        f"Exam instance directory not found for {instance_id} under {_INSTANCES_ROOT}; "
        "build the model.lp files with `python -m scripts.prepare_data`"
    )


def _weight_view(data: dict[str, Any]) -> dict[str, float]:
    weights = dict(data.get("weights") or {})
    return {
        "alpha": float(weights.get("alpha", 0.0)),
        "beta": float(weights.get("beta", 0.0)),
        "gamma1": float(weights.get("gamma1", 0.0)),
        "gamma2": float(weights.get("gamma2", 0.0)),
        "delta": float(weights.get("delta", 0.0)),
    }
