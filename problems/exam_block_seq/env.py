"""Thin environment wrapper for exam block sequencing."""
from __future__ import annotations

import os
from typing import Dict, Tuple

import gurobipy as gp
from gurobipy import GRB

from framework.core import (
    BaseEnv,
    StructuredModel,
)
from problems.exam_block_seq.patch_runtime import solve_exam_model
from problems.exam_block_seq.structured import build_exam_structured_model


class ExamBlockSeqEnv(BaseEnv):
    def __init__(
        self,
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
        time_limit: int = 600,
        threads: int | None = None,
        instance_id: str | None = None,
    ):
        self.name = "exam_block_seq"
        self.lp_path = lp_path
        self.blocks = list(blocks)
        self.slots_per_day = slots_per_day
        self.slot_times = list(slot_times or [])
        self.triple_24_start = list(triple_24_start)
        self.triple_day_start = list(triple_day_start)
        self.eve_morn_start = list(eve_morn_start)
        self.other_b2b_start = list(other_b2b_start)
        self.base_weights = dict(weights)
        self.reserved_slots = list(reserved_slots or [])
        self.real_blocks = int(real_blocks) if real_blocks is not None else len(blocks)
        self.virtual_blocks = list(virtual_blocks or [])
        self.large_blocks = list(large_blocks or [])
        self.early_slots = list(early_slots or [])
        self.block_enrollment = dict(block_enrollment or {})
        self.block_num_exams = dict(block_num_exams or {})
        self.pair_counts = dict(pair_counts or {})
        self.triplet_counts = dict(triplet_counts or {})
        self.frontload_block_size_cutoff = frontload_block_size_cutoff
        self.frontload_slot_cutoff = frontload_slot_cutoff
        self.time_limit = int(time_limit)
        self.threads = int(threads) if threads is not None else None
        self.instance_id = instance_id or os.path.splitext(os.path.basename(lp_path))[0]
        self._cached_model: "gp.Model | None" = None
        self.last_solve_meta: Dict[str, float | int] = {}

    def build_structured_model(self) -> StructuredModel:
        return build_exam_structured_model(
            lp_path=self.lp_path,
            blocks=self.blocks,
            slots_per_day=self.slots_per_day,
            triple_24_start=self.triple_24_start,
            triple_day_start=self.triple_day_start,
            eve_morn_start=self.eve_morn_start,
            other_b2b_start=self.other_b2b_start,
            weights=self.base_weights,
            slot_times=self.slot_times,
            reserved_slots=self.reserved_slots,
            real_blocks=self.real_blocks,
            virtual_blocks=self.virtual_blocks,
            large_blocks=self.large_blocks,
            early_slots=self.early_slots,
            block_enrollment=self.block_enrollment,
            block_num_exams=self.block_num_exams,
            pair_counts=self.pair_counts,
            triplet_counts=self.triplet_counts,
            frontload_block_size_cutoff=self.frontload_block_size_cutoff,
            frontload_slot_cutoff=self.frontload_slot_cutoff,
            instance_id=self.instance_id,
        )

    def load_gurobi_model(self) -> "gp.Model":
        if self._cached_model is None:
            self._cached_model = gp.read(self.lp_path)
        return self._cached_model

    def solve(self, model: StructuredModel) -> Tuple[float, Dict[int, int], Dict[str, float | int]]:
        if self.threads is not None:
            solver_params = dict(model.parameters.get("solver_params") or {})
            solver_params["Threads"] = self.threads
            model.parameters["solver_params"] = solver_params
        objective, solution, meta = solve_exam_model(
            self.load_gurobi_model(),
            model,
            base_weights=self.base_weights,
            default_time_limit=self.time_limit,
        )
        self.last_solve_meta = dict(meta)
        return objective, solution, dict(self.last_solve_meta)
