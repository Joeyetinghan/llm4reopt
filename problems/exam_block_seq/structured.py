"""Exam block sequencing structured-model helpers for patch editing."""

from __future__ import annotations

import os
from typing import Dict

from framework.core import (
    ConstraintFamily,
    ObjectiveComponent,
    StructuredModel,
    VariableFamily,
    VariableType,
    register_constraint_family,
    register_objective_component,
    register_parameter,
    register_var_family,
)
from problems.exam_block_seq.constraint_families import (
    build_frontload_constraint_family,
    build_reserved_virtual_slot_constraint_family,
    empty_reserved_virtual_slot_family,
    empty_slot_load_cap_family,
)


def build_exam_structured_model(
    *,
    lp_path: str,
    blocks: list[int],
    slots_per_day: int,
    triple_24_start: list[int],
    triple_day_start: list[int],
    eve_morn_start: list[int],
    other_b2b_start: list[int],
    weights: Dict[str, float],
    slot_times: list[str] | None = None,
    reserved_slots: list[int] | None = None,
    real_blocks: int | None = None,
    virtual_blocks: list[int] | None = None,
    large_blocks: list[int] | None = None,
    early_slots: list[int] | None = None,
    block_enrollment: Dict[int, float] | None = None,
    block_num_exams: Dict[int, int] | None = None,
    pair_counts: Dict[tuple[int, int], float] | None = None,
    triplet_counts: Dict[tuple[int, int, int], float] | None = None,
    frontload_block_size_cutoff: float | None = None,
    frontload_slot_cutoff: int | None = None,
    instance_id: str | None = None,
) -> StructuredModel:
    variables = {
        "x": VariableFamily(
            name="x",
            index_set=[],
            var_type=VariableType.BINARY,
            lower_bounds={},
            upper_bounds={},
            desc="Assignment variable x[i,j,k,s] placing a block triplet into slot s.",
            tags={"block_seq", "assignment"},
        ),
        "y": VariableFamily(
            name="y",
            index_set=[],
            var_type=VariableType.BINARY,
            lower_bounds={},
            upper_bounds={},
            desc="Triplet linkage variable y[i,j,k].",
            tags={"block_seq", "triplet"},
        ),
        "z": VariableFamily(
            name="z",
            index_set=[],
            var_type=VariableType.BINARY,
            lower_bounds={},
            upper_bounds={},
            desc="Four-slot sequence linkage variable z[i,j,k,l].",
            tags={"block_seq", "quad"},
        ),
        "schedule": VariableFamily(
            name="schedule",
            index_set=blocks,
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Slot assignment summary variable slot_assignment[s].",
            tags={"block_seq", "output"},
        ),
        "block_assigned": VariableFamily(
            name="block_assigned",
            index_set=blocks,
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Assigned slot index for each block.",
            tags={"block_seq", "output"},
        ),
        "block_diff": VariableFamily(
            name="block_diff",
            index_set=[],
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Pairwise slot distance between blocks.",
            tags={"block_seq", "spacing"},
        ),
        "block_diff_large": VariableFamily(
            name="block_diff_large",
            index_set=[],
            var_type=VariableType.BINARY,
            lower_bounds={},
            upper_bounds={},
            desc="Binary indicator for large block gaps.",
            tags={"block_seq", "spacing"},
        ),
        "triple_in_day": VariableFamily(
            name="triple_in_day",
            index_set=["all"],
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Total triples within a day.",
            tags={"block_seq", "penalty"},
        ),
        "triple_in_24hr": VariableFamily(
            name="triple_in_24hr",
            index_set=["all"],
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Total triples within 24 hours.",
            tags={"block_seq", "penalty"},
        ),
        "b2b_eveMorn": VariableFamily(
            name="b2b_eveMorn",
            index_set=["all"],
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Back-to-back evening-to-morning penalty variable.",
            tags={"block_seq", "penalty"},
        ),
        "b2b_other": VariableFamily(
            name="b2b_other",
            index_set=["all"],
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Other back-to-back penalty variable.",
            tags={"block_seq", "penalty"},
        ),
        "three_exams_four_slots": VariableFamily(
            name="three_exams_four_slots",
            index_set=["all"],
            var_type=VariableType.INTEGER,
            lower_bounds={},
            upper_bounds={},
            desc="Three exams in four slots penalty variable.",
            tags={"block_seq", "penalty"},
        ),
    }

    constraints = {
        "each_i": ConstraintFamily(
            name="each_i",
            index_set=blocks,
            lhs_spec="sum_{j,k,s} x[i,j,k,s]",
            rhs_spec=1,
            sense="=",
            desc="Each block appears once in position i.",
            tags={"block_seq"},
        ),
        "each_j": ConstraintFamily(
            name="each_j",
            index_set=blocks,
            lhs_spec="sum_{i,k,s} x[i,j,k,s]",
            rhs_spec=1,
            sense="=",
            desc="Each block appears once in position j.",
            tags={"block_seq"},
        ),
        "each_k": ConstraintFamily(
            name="each_k",
            index_set=blocks,
            lhs_spec="sum_{i,j,s} x[i,j,k,s]",
            rhs_spec=1,
            sense="=",
            desc="Each block appears once in position k.",
            tags={"block_seq"},
        ),
        "each_slot": ConstraintFamily(
            name="each_slot",
            index_set=blocks,
            lhs_spec="sum_{i,j,k} x[i,j,k,s]",
            rhs_spec={s: 1 for s in blocks},
            sense="=",
            desc="Each slot gets exactly one block triple.",
            tags={"block_seq"},
        ),
        "continuity": ConstraintFamily(
            name="continuity",
            index_set=[],
            lhs_spec="Link consecutive triples across adjacent slots.",
            rhs_spec="-",
            sense="=",
            desc="Triple continuity across adjacent slots.",
            tags={"block_seq"},
        ),
        "frontload": build_frontload_constraint_family(
            large_blocks=list(large_blocks or []),
            early_slots=list(early_slots or []),
        ),
        "reserved_virtual_slot": (
            build_reserved_virtual_slot_constraint_family(
                virtual_blocks=list(virtual_blocks or []),
                reserved_slots=list(reserved_slots or []),
            )
            if reserved_slots and virtual_blocks
            else empty_reserved_virtual_slot_family()
        ),
        "slot_load_cap": empty_slot_load_cap_family(),
    }

    objectives = {
        "alpha": ObjectiveComponent(
            name="alpha",
            weight=float(weights.get("alpha", 10)),
            spec={"metric": "triple_in_day"},
            desc="Weight for triple-in-day penalty.",
            tags={"block_seq", "objective"},
        ),
        "beta": ObjectiveComponent(
            name="beta",
            weight=float(weights.get("beta", 10)),
            spec={"metric": "triple_in_24hr"},
            desc="Weight for triple-in-24hr penalty.",
            tags={"block_seq", "objective"},
        ),
        "gamma1": ObjectiveComponent(
            name="gamma1",
            weight=float(weights.get("gamma1", 1)),
            spec={"metric": "b2b_eveMorn"},
            desc="Weight for evening-to-morning back-to-back penalty.",
            tags={"block_seq", "objective"},
        ),
        "gamma2": ObjectiveComponent(
            name="gamma2",
            weight=float(weights.get("gamma2", 1)),
            spec={"metric": "b2b_other"},
            desc="Weight for other back-to-back penalty.",
            tags={"block_seq", "objective"},
        ),
        "delta": ObjectiveComponent(
            name="delta",
            weight=float(weights.get("delta", 5)),
            spec={"metric": "three_exams_four_slots"},
            desc="Weight for three exams in four slots penalty.",
            tags={"block_seq", "objective"},
        ),
    }

    model = StructuredModel()
    for family in variables.values():
        register_var_family(model, family)
    for family in constraints.values():
        register_constraint_family(model, family)
    for objective in objectives.values():
        register_objective_component(model, objective)

    register_parameter(
        model,
        "instance_id",
        instance_id or os.path.splitext(os.path.basename(lp_path))[0],
        desc="Stable instance identifier for this exam scheduling LP.",
        tags={"block_seq", "instance"},
    )
    register_parameter(
        model,
        "lp_path",
        lp_path,
        desc="Filesystem path to the compiled LP used for solving.",
        tags={"block_seq", "artifact"},
    )
    register_parameter(
        model,
        "blocks",
        list(blocks),
        desc="Block identifiers to be assigned into exam slots.",
        tags={"block_seq", "blocks"},
    )
    register_parameter(
        model,
        "real_blocks",
        int(real_blocks) if real_blocks is not None else len(blocks),
        desc="Number of real exam blocks before any dummy blocks.",
        tags={"block_seq", "blocks"},
    )
    register_parameter(
        model,
        "virtual_blocks",
        list(virtual_blocks or []),
        desc="Virtual blocks representing empty slots in the sequencing instance.",
        tags={"block_seq", "blocks"},
    )
    register_parameter(
        model,
        "large_blocks",
        list(large_blocks or []),
        desc="Blocks marked as large exams for front-loading constraints.",
        tags={"block_seq", "blocks", "frontload"},
    )
    register_parameter(
        model,
        "early_slots",
        list(early_slots or []),
        desc="Slots considered early enough for front-loading large exams.",
        tags={"block_seq", "calendar", "frontload"},
    )
    register_parameter(
        model,
        "slots_per_day",
        slots_per_day,
        desc="Number of exam slots available per day.",
        tags={"block_seq", "calendar"},
    )
    register_parameter(
        model,
        "slot_times",
        list(slot_times or []),
        desc="Human-readable slot labels in day order.",
        tags={"block_seq", "calendar"},
    )
    register_parameter(
        model,
        "triple_24_start",
        list(triple_24_start),
        desc="Slots that start a 24-hour triple window.",
        tags={"block_seq", "calendar"},
    )
    register_parameter(
        model,
        "triple_day_start",
        list(triple_day_start),
        desc="Slots that start a same-day triple window.",
        tags={"block_seq", "calendar"},
    )
    register_parameter(
        model,
        "eve_morn_start",
        list(eve_morn_start),
        desc="Slots that start evening-to-morning back-to-back windows.",
        tags={"block_seq", "calendar"},
    )
    register_parameter(
        model,
        "other_b2b_start",
        list(other_b2b_start),
        desc="Slots that start other back-to-back windows.",
        tags={"block_seq", "calendar"},
    )
    register_parameter(model, "alpha", float(weights.get("alpha", 10)), desc="Weight for triple-in-day penalties.", tags={"block_seq", "weight"})
    register_parameter(model, "beta", float(weights.get("beta", 10)), desc="Weight for triple-in-24hr penalties.", tags={"block_seq", "weight"})
    register_parameter(model, "gamma1", float(weights.get("gamma1", 1)), desc="Weight for evening-to-morning back-to-back penalties.", tags={"block_seq", "weight"}, aliases={"b2b_eve_morn_penalty"})
    register_parameter(model, "gamma2", float(weights.get("gamma2", 1)), desc="Weight for other back-to-back penalties.", tags={"block_seq", "weight"})
    register_parameter(model, "delta", float(weights.get("delta", 5)), desc="Weight for three-exams-in-four-slots penalties.", tags={"block_seq", "weight"})
    register_parameter(model, "vega", float(weights.get("vega", 1)), desc="Additional tuning weight preserved from the original exam formulation.", tags={"block_seq", "weight"})
    register_parameter(model, "theta", float(weights.get("theta", 2)), desc="Additional tuning weight preserved from the original exam formulation.", tags={"block_seq", "weight"})
    register_parameter(model, "lambda_large1", float(weights.get("lambda_large1", 1)), desc="Large-gap penalty weight variant 1.", tags={"block_seq", "weight"})
    register_parameter(model, "lambda_large2", float(weights.get("lambda_large2", 1)), desc="Large-gap penalty weight variant 2.", tags={"block_seq", "weight"})
    register_parameter(model, "lambda_big", float(weights.get("lambda_big", 1000)), desc="Large constant used in block sequencing penalties.", tags={"block_seq", "weight"})
    register_parameter(model, "frontload_block_size_cutoff", frontload_block_size_cutoff, desc="Enrollment cutoff used to mark large blocks for front-loading.", tags={"block_seq", "frontload"})
    register_parameter(model, "frontload_slot_cutoff", frontload_slot_cutoff, desc="Last slot index considered early for front-loading large blocks.", tags={"block_seq", "frontload"})
    register_parameter(model, "reserved_slots", list(reserved_slots or []), desc="Slots reserved for deterministic virtual-block assignment.", tags={"block_seq", "availability"}, aliases={"forbidden_slots"})
    register_parameter(model, "block_enrollment", dict(block_enrollment or {}), desc="Enrollment by block from the sequencing instance summary.", tags={"block_seq", "blocks"})
    register_parameter(model, "block_num_exams", dict(block_num_exams or {}), desc="Number of exams assigned to each block.", tags={"block_seq", "blocks"})
    register_parameter(model, "pair_counts", dict(pair_counts or {}), desc="Base pairwise co-enrollment counts keyed by block pairs.", tags={"block_seq", "data"})
    register_parameter(model, "triplet_counts", dict(triplet_counts or {}), desc="Base triplet co-enrollment counts keyed by block triples.", tags={"block_seq", "data"})
    register_parameter(model, "p", {}, desc="Optional pairwise penalty overrides keyed by block pairs.", tags={"block_seq", "override"})
    register_parameter(model, "t", {}, desc="Optional triplet penalty overrides keyed by block triplets.", tags={"block_seq", "override"})

    model.artifacts.update({"lp_path": lp_path})
    model.supports.update(
        {
            "solver_backend": "gurobi",
            "supports_warm_start": True,
            "supports_tuned_solver": False,
        }
    )
    model.extras["base_parameters"] = {
        "alpha": float(weights.get("alpha", 10)),
        "beta": float(weights.get("beta", 10)),
        "gamma1": float(weights.get("gamma1", 1)),
        "gamma2": float(weights.get("gamma2", 1)),
        "delta": float(weights.get("delta", 5)),
    }
    return model
