"""Transportation structured-model helpers for patch editing."""

from __future__ import annotations

from typing import Dict, List

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


def build_transport_structured_model(
    *,
    plants: List[str],
    customers: List[str],
    supply: Dict[str, float],
    demand: Dict[str, float],
    costs: Dict[str, Dict[str, float]],
) -> StructuredModel:
    indices = [(i, j) for i in plants for j in customers]
    model = StructuredModel()
    register_var_family(
        model,
        VariableFamily(
            name="flows",
            index_set=indices,
            var_type=VariableType.CONTINUOUS,
            lower_bounds={idx: 0.0 for idx in indices},
            upper_bounds={idx: float("inf") for idx in indices},
            desc="Flow from plant i to customer j",
            tags={"transport", "flow"},
            aliases={"shipments", "lanes"},
        ),
    )

    supply_constraint = ConstraintFamily(
        name="supply_constraints",
        index_set=list(plants),
        lhs_spec={plant: [(plant, cust) for cust in customers] for plant in plants},
        rhs_spec=dict(supply),
        sense="<=",
        desc="Supply availability per plant",
        tags={"transport", "supply"},
        aliases={"plant_supply"},
    )

    demand_constraint = ConstraintFamily(
        name="demand_constraints",
        index_set=list(customers),
        lhs_spec={cust: [(plant, cust) for plant in plants] for cust in customers},
        rhs_spec=dict(demand),
        sense=">=",
        desc="Demand requirements per customer",
        tags={"transport", "demand"},
        aliases={"customer_demand"},
    )

    objective = ObjectiveComponent(
        name="transport_cost",
        weight=1.0,
        spec={"coeffs": {idx: costs[idx[0]][idx[1]] for idx in indices}},
        desc="Total transportation cost",
        tags={"transport", "cost"},
        aliases={"total_cost"},
    )

    register_constraint_family(model, supply_constraint)
    register_constraint_family(model, demand_constraint)
    register_objective_component(model, objective)

    register_parameter(
        model,
        "plants",
        list(plants),
        desc="Plant identifiers used in the transportation network.",
        tags={"transport", "plants"},
    )
    register_parameter(
        model,
        "customers",
        list(customers),
        desc="Customer identifiers receiving product.",
        tags={"transport", "customers"},
    )
    register_parameter(
        model,
        "supply",
        dict(supply),
        desc="Available supply by plant.",
        tags={"transport", "supply"},
        aliases={"plant_capacity"},
    )
    register_parameter(
        model,
        "demand",
        dict(demand),
        desc="Required demand by customer.",
        tags={"transport", "demand"},
        aliases={"customer_demand"},
    )
    register_parameter(
        model,
        "costs",
        {p: dict(c) for p, c in costs.items()},
        desc="Transportation cost by plant-customer lane.",
        tags={"transport", "cost"},
        aliases={"lane_costs"},
    )

    model.supports.update(
        {
            "solver_backend": "gurobi",
            "supports_warm_start": True,
            "supports_tuned_solver": False,
        }
    )
    return model
