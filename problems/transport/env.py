"""Transportation environment implementing BaseEnv."""
from __future__ import annotations

from typing import Dict, List, Tuple

from framework.core import BaseEnv, StructuredModel
from problems.transport.patch_runtime import solve_transport_model
from problems.transport.structured import build_transport_structured_model


class TransportationEnv(BaseEnv):
    def __init__(self, plants: List[str], customers: List[str], supply: Dict[str, float], demand: Dict[str, float], costs: Dict[str, Dict[str, float]]):
        self.name = "transportation"
        self.plants = plants
        self.customers = customers
        self.supply = supply
        self.demand = demand
        self.costs = costs

    def _flow_indices(self) -> List[Tuple[str, str]]:
        return [(i, j) for i in self.plants for j in self.customers]

    def build_structured_model(self) -> StructuredModel:
        return build_transport_structured_model(
            plants=self.plants,
            customers=self.customers,
            supply=self.supply,
            demand=self.demand,
            costs=self.costs,
        )

    def solve(self, model: StructuredModel) -> Tuple[float, Dict[Tuple[str, str], float]]:
        objective, solution, meta = solve_transport_model(model)
        self.last_solve_meta = dict(meta)
        return objective, solution
