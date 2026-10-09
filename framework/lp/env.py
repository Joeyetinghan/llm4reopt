"""Generic LP-backed environment for runtime bundles."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import gurobipy as gp
from gurobipy import GRB

from framework.core import (
    BaseEnv,
    ConstraintFamily,
    ObjectiveComponent,
    SolveFailureError,
    StructuredModel,
    VariableFamily,
    VariableType,
)
from framework.lp.executor import apply_patches_to_gurobi


LP_PATTERN_OPS = (
    "FIX_VARIABLES_BY_PATTERN",
    "UPDATE_CONSTRAINT_RHS_BY_PATTERN",
    "UPDATE_COEFFICIENT",
)


class GenericLPEnv(BaseEnv):
    def __init__(
        self,
        *,
        lp_path: str | Path,
        name: str = "lp_bundle",
        problem_id: str | None = None,
        time_limit: int = 3600,
        mip_gap: float = 0.01,
    ):
        self.name = name
        self.problem_id = problem_id or name
        self.lp_path = str(Path(lp_path).expanduser().resolve())
        self.time_limit = int(time_limit)
        self.mip_gap = float(mip_gap)
        self._cached_model: "gp.Model | None" = None
        self._cached_inventory: dict[str, Any] | None = None
        self.last_solve_meta: dict[str, Any] = {}

    def build_structured_model(self) -> StructuredModel:
        inventory = self.inventory()
        variables = {
            family["name"]: VariableFamily(
                name=family["name"],
                index_set=[],
                var_type=family["var_type"],
                lower_bounds={},
                upper_bounds={},
                desc=family["description"],
                tags={"lp", "runtime_bundle"},
            )
            for family in inventory["variable_families"]
        }
        constraints = {
            family["name"]: ConstraintFamily(
                name=family["name"],
                index_set=[],
                lhs_spec=family["sample_names"],
                rhs_spec="-",
                sense=family["sense"],
                desc=family["description"],
                tags={"lp", "runtime_bundle"},
            )
            for family in inventory["constraint_families"]
        }
        objectives = {
            "lp_objective": ObjectiveComponent(
                name="lp_objective",
                weight=1.0,
                spec={
                    "sense": inventory["objective_sense"],
                    "nonzero_objective_vars": inventory["objective_nonzero_var_count"],
                },
                desc="Objective imported from the LP artifact.",
                tags={"lp", "objective"},
            )
        }
        parameters = {
            "lp_path": self.lp_path,
            "problem_id": self.problem_id,
            "time_limit": self.time_limit,
            "mip_gap": self.mip_gap,
            "lp_inventory": inventory,
            "supported_lp_patch_ops": list(LP_PATTERN_OPS),
        }
        return StructuredModel(
            variables=variables,
            constraints=constraints,
            objectives=objectives,
            parameters=parameters,
            artifacts={"lp_path": self.lp_path},
            extras=_lp_extras(inventory),
        )

    def load_gurobi_model(self) -> "gp.Model":
        if self._cached_model is None:
            self._cached_model = gp.read(self.lp_path)
        return self._cached_model

    def inventory(self) -> dict[str, Any]:
        if self._cached_inventory is None:
            self._cached_inventory = summarize_lp_model(self.load_gurobi_model())
        return dict(self._cached_inventory)

    def solve(
        self,
        model: StructuredModel,
        patches: list | None = None,
    ):
        grb = self.load_gurobi_model().copy()
        grb.Params.OutputFlag = int(bool(model.parameters.get("output_flag", 0)))
        grb.Params.TimeLimit = float(model.parameters.get("time_limit", self.time_limit))
        grb.Params.MIPGap = float(model.parameters.get("mip_gap", self.mip_gap))
        apply_solver_params(grb, model.parameters.get("solver_params"))

        patch_logs: list[dict[str, Any]] = []
        if patches:
            patch_logs = apply_patches_to_gurobi(grb, patches)

        solution, solve_meta = optimize_gurobi_model(grb)
        solution["patch_logs"] = patch_logs
        if "objective" in solution and solution.get("objective") not in {float("inf"), float("-inf")}:
            solution["assignments"] = _extract_solution_assignments(grb)
        self.last_solve_meta = dict(solve_meta)

        return solution["objective"], solution


def summarize_lp_model(model: "gp.Model") -> dict[str, Any]:
    variable_families = _build_variable_family_summary(model)
    constraint_families = _build_constraint_family_summary(model)
    return {
        "lp_path": getattr(model, "ModelName", ""),
        "num_variables": int(model.NumVars),
        "num_constraints": int(model.NumConstrs),
        "objective_sense": "minimize" if model.ModelSense >= 0 else "maximize",
        "objective_nonzero_var_count": int(sum(1 for variable in model.getVars() if abs(variable.Obj) > 1e-12)),
        "variable_families": variable_families,
        "constraint_families": constraint_families,
        "sample_variables": [variable.VarName for variable in model.getVars()[:10]],
        "sample_constraints": [constraint.ConstrName for constraint in model.getConstrs()[:10]],
        "snippets": extract_lp_snippets(model, variable_families, constraint_families),
    }


def render_inventory_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "Runtime LP inventory:",
        f"- variables: {inventory.get('num_variables', 0)}",
        f"- constraints: {inventory.get('num_constraints', 0)}",
        f"- objective sense: {inventory.get('objective_sense', 'unknown')}",
    ]

    variable_families = inventory.get("variable_families") or []
    if variable_families:
        lines.append("Variable families:")
        for family in variable_families[:10]:
            lines.append(
                f"- {family['name']}: type={family['var_type'].value if hasattr(family['var_type'], 'value') else family['var_type']}, count={family['count']}, samples={family['sample_names']}"
            )

    constraint_families = inventory.get("constraint_families") or []
    if constraint_families:
        lines.append("Constraint families:")
        for family in constraint_families[:10]:
            lines.append(
                f"- {family['name']}: sense={family['sense']}, count={family['count']}, samples={family['sample_names']}"
            )

    return "\n".join(lines)


def render_lp_snippets_markdown(snippets: dict[str, Any]) -> str:
    lines = ["Representative LP snippets:"]

    variable_snippets = snippets.get("variable_families") or {}
    if variable_snippets:
        lines.append("Variable-family snippets:")
        for family_name, entries in variable_snippets.items():
            lines.append(f"- {family_name}:")
            for entry in entries:
                lines.append(f"  - {entry}")

    constraint_snippets = snippets.get("constraint_families") or {}
    if constraint_snippets:
        lines.append("Constraint-family snippets:")
        for family_name, entries in constraint_snippets.items():
            lines.append(f"- {family_name}:")
            for entry in entries:
                lines.append(f"  - {entry}")

    return "\n".join(lines)


def render_lp_patch_examples_markdown(examples: dict[str, str]) -> str:
    if not examples:
        return ""
    lines = ["Deterministic LP patch examples:"]
    for op_name, example in examples.items():
        lines.extend([f"{op_name}:", example])
    return "\n".join(lines)


def build_lp_patch_examples() -> dict[str, str]:
    # Intentionally empty for now. Problem-specific examples should only be added
    # once they are validated against the actual LP naming patterns.
    # Keeping this helper allows future reintroduction without changing the
    # representation path, but no LP examples are emitted today.
    return {}


def _lp_extras(inventory: dict[str, Any]) -> dict[str, Any]:
    extras: dict[str, Any] = {
        "lp_inventory": inventory,
        "lp_snippets": inventory.get("snippets", {}),
    }
    patch_examples = build_lp_patch_examples()
    if patch_examples:
        extras["lp_patch_examples"] = patch_examples
    return extras


def extract_lp_snippets(
    model: "gp.Model",
    variable_families: list[dict[str, Any]],
    constraint_families: list[dict[str, Any]],
) -> dict[str, Any]:
    variable_lookup = _sample_objects_by_family(model.getVars(), lambda variable: variable.VarName)
    constraint_lookup = _sample_objects_by_family(model.getConstrs(), lambda constr: constr.ConstrName)

    variable_snippets: dict[str, list[str]] = {}
    for family in variable_families[:8]:
        name = family["name"]
        sample_object = variable_lookup.get(name)
        if sample_object is None:
            continue
        variable_snippets[name] = [_render_variable_snippet(model, sample_object)]

    constraint_snippets: dict[str, list[str]] = {}
    for family in constraint_families[:8]:
        name = family["name"]
        sample_object = constraint_lookup.get(name)
        if sample_object is None:
            continue
        constraint_snippets[name] = [_render_constraint_snippet(model, sample_object)]

    return {
        "variable_families": variable_snippets,
        "constraint_families": constraint_snippets,
    }


def _build_variable_family_summary(model: "gp.Model") -> list[dict[str, Any]]:
    families: dict[str, list[Any]] = {}
    for variable in model.getVars():
        family = _token_family(variable.VarName)
        families.setdefault(family, []).append(variable)

    ordered = sorted(families.items(), key=lambda item: (-len(item[1]), item[0]))
    summary: list[dict[str, Any]] = []
    for family_name, variables in ordered[:25]:
        summary.append(
            {
                "name": family_name,
                "count": len(variables),
                "var_type": _gurobi_var_type_to_enum(variables[0].VType),
                "sample_names": [variable.VarName for variable in variables[:5]],
                "description": f"Variables whose LP names share the prefix '{family_name}'.",
            }
        )
    return summary


def _build_constraint_family_summary(model: "gp.Model") -> list[dict[str, Any]]:
    families: dict[str, list[Any]] = {}
    for constraint in model.getConstrs():
        family = _token_family(constraint.ConstrName)
        families.setdefault(family, []).append(constraint)

    ordered = sorted(families.items(), key=lambda item: (-len(item[1]), item[0]))
    summary: list[dict[str, Any]] = []
    for family_name, constraints in ordered[:25]:
        senses = Counter(_sense_symbol(constraint.Sense) for constraint in constraints)
        sense = max(senses.items(), key=lambda item: item[1])[0] if senses else "<="
        summary.append(
            {
                "name": family_name,
                "count": len(constraints),
                "sense": sense,
                "sample_names": [constraint.ConstrName for constraint in constraints[:5]],
                "description": f"Constraints whose LP names share the prefix '{family_name}'.",
            }
        )
    return summary


def _sample_objects_by_family(objects: list[Any], name_getter) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    for obj in objects:
        family = _token_family(name_getter(obj))
        samples.setdefault(family, obj)
    return samples


def _render_variable_snippet(model: "gp.Model", variable: Any) -> str:
    snippet = (
        f"{variable.VarName}: type={_gurobi_var_type_to_enum(variable.VType).value}, "
        f"lb={float(variable.LB):.4g}, ub={float(variable.UB):.4g}, obj={float(variable.Obj):.4g}"
    )
    try:
        column = model.getCol(variable)
    except Exception:
        return snippet

    terms: list[str] = []
    size = min(column.size(), 5)
    for idx in range(size):
        constraint = column.getConstr(idx)
        coeff = column.getCoeff(idx)
        terms.append(f"{float(coeff):.4g}*{constraint.ConstrName}")
    if terms:
        snippet += " | column: " + " + ".join(terms)
    return snippet


def _render_constraint_snippet(model: "gp.Model", constraint: Any) -> str:
    sense = _sense_symbol(constraint.Sense)
    try:
        row = model.getRow(constraint)
    except Exception:
        return f"{constraint.ConstrName}: sense={sense}"

    terms: list[str] = []
    size = min(row.size(), 6)
    for idx in range(size):
        variable = row.getVar(idx)
        coeff = row.getCoeff(idx)
        terms.append(f"{float(coeff):.4g}*{variable.VarName}")
    rhs = getattr(constraint, "RHS", None)
    rhs_text = f"{float(rhs):.4g}" if isinstance(rhs, (int, float)) else "?"
    lhs = " + ".join(terms) if terms else "0"
    return f"{constraint.ConstrName}: {lhs} {sense} {rhs_text}"


def _token_family(name: str) -> str:
    if "[" in name:
        return name.split("[", 1)[0] or name
    if "_" in name:
        return name.split("_", 1)[0] or name
    return name


def _gurobi_var_type_to_enum(value: str) -> VariableType:
    if value == "B":
        return VariableType.BINARY
    if value == "I":
        return VariableType.INTEGER
    return VariableType.CONTINUOUS


def _sense_symbol(value: str) -> str:
    if value in {"<", "<="}:
        return "<="
    if value in {">", ">="}:
        return ">="
    return "="


def _extract_solution_assignments(model: "gp.Model") -> dict[str, float]:
    return {
        variable.VarName: float(variable.X)
        for variable in model.getVars()
        if abs(variable.X) > 1e-9
    }


def apply_solver_params(model: "gp.Model", solver_params: Any) -> None:
    if not isinstance(solver_params, dict):
        return
    for name, value in solver_params.items():
        if name == "OutputFlag":
            model.Params.OutputFlag = int(bool(value))
            continue
        try:
            setattr(model.Params, str(name), value)
        except Exception:
            continue


def extract_lp_solve_meta(model: "gp.Model") -> dict[str, Any]:
    meta: dict[str, Any] = {
        "status": int(model.Status),
        "runtime": float(getattr(model, "Runtime", 0.0)),
        "sol_count": int(getattr(model, "SolCount", 0)),
    }
    try:
        if model.IsMIP:
            meta["mip_gap"] = float(model.MIPGap)
    except Exception:
        pass
    if meta["sol_count"] > 0:
        try:
            meta["objective"] = float(model.ObjVal)
        except Exception:
            pass
    return meta


def optimize_gurobi_model(model: "gp.Model") -> tuple[dict[str, Any], dict[str, Any]]:
    model.optimize()
    if model.Status == GRB.INF_OR_UNBD:
        model.Params.Presolve = 0
        model.optimize()

    solution: dict[str, Any] = {
        "status": int(model.Status),
    }
    if model.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT) and model.SolCount > 0:
        solution["objective"] = float(model.ObjVal)
        solution["gap"] = float(model.MIPGap) if model.IsMIP else 0.0
    else:
        solution["objective"] = float("inf") if model.ModelSense >= 0 else float("-inf")

    return solution, extract_lp_solve_meta(model)


def solve_gurobi_model(
    model: "gp.Model",
    *,
    solution_updates: dict[str, Any] | None = None,
    failure_prefix: str = "LP solve failed",
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    solution, solve_meta = optimize_gurobi_model(model)
    if solution_updates:
        solution.update(solution_updates)
    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT):
        raise SolveFailureError(
            f"{failure_prefix} with status {model.Status}",
            solve_meta=solve_meta,
        )
    if model.SolCount == 0:
        raise SolveFailureError(
            f"No incumbent solution available at status {model.Status}",
            solve_meta=solve_meta,
        )
    objective = float(solution["objective"])
    solve_meta["objective"] = objective
    return objective, solution, solve_meta
