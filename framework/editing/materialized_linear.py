"""Execution helpers for materialized linear constraint families."""

from __future__ import annotations

from typing import Any, Callable, Iterable

import gurobipy as gp

from framework.core import ConstraintFamily, StructuredModel


MATERIALIZED_LINEAR_KINDS = {"materialized_linear", "materialized_linear_family"}


def apply_materialized_linear_constraint_families(
    model: gp.Model,
    structured: StructuredModel,
    *,
    resolve_variable: Callable[[str, Any], gp.Var | None],
    expand_term: Callable[[dict[str, Any]], Iterable[tuple[gp.Var, float]] | None] | None = None,
) -> list[str]:
    added: list[str] = []
    for family in structured.constraints.values():
        lhs_spec = family.lhs_spec
        if not _is_materialized_linear_family(lhs_spec):
            continue
        rows = lhs_spec.get("rows")
        if not isinstance(rows, dict):
            raise RuntimeError(
                f"Constraint family '{family.name}' must provide dict rows for materialized linear execution."
            )
        row_indices = list(family.index_set or rows.keys())
        for row_index in row_indices:
            row_terms = list(rows.get(row_index) or [])
            expr = gp.LinExpr()
            for term in row_terms:
                coeff = float(term.get("coeff", 1.0))
                expanded_terms = expand_term(term) if expand_term is not None else None
                if expanded_terms is not None:
                    expanded_any = False
                    for variable, scale in expanded_terms:
                        if variable is None:
                            continue
                        expr += coeff * float(scale) * variable
                        expanded_any = True
                    if not expanded_any:
                        raise RuntimeError(
                            f"Constraint family '{family.name}' references missing variable "
                            f"{term.get('variable')}[{term.get('index')!r}]"
                        )
                    continue

                variable = resolve_variable(str(term.get("variable")), term.get("index"))
                if variable is None:
                    raise RuntimeError(
                        f"Constraint family '{family.name}' references missing variable "
                        f"{term.get('variable')}[{term.get('index')!r}]"
                    )
                expr += coeff * variable
            rhs = _rhs_for_row(family, row_index)
            name = _constraint_name(family, row_index)
            if family.sense == "<=":
                model.addConstr(expr <= rhs, name=name)
            elif family.sense == ">=":
                model.addConstr(expr >= rhs, name=name)
            elif family.sense == "=":
                model.addConstr(expr == rhs, name=name)
            else:
                raise RuntimeError(
                    f"Constraint family '{family.name}' uses unsupported sense {family.sense!r}."
                )
            added.append(name)
    if added:
        model.update()
    return added


def _is_materialized_linear_family(lhs_spec: Any) -> bool:
    return isinstance(lhs_spec, dict) and str(lhs_spec.get("kind") or "").strip().lower() in MATERIALIZED_LINEAR_KINDS


def _rhs_for_row(family: ConstraintFamily, row_index: Any) -> float:
    rhs_spec = family.rhs_spec
    if isinstance(rhs_spec, dict):
        if row_index not in rhs_spec:
            raise RuntimeError(f"Constraint family '{family.name}' is missing rhs for row {row_index!r}.")
        return float(rhs_spec[row_index])
    return float(rhs_spec)


def _constraint_name(family: ConstraintFamily, row_index: Any) -> str:
    if row_index in {None, ""}:
        return family.name
    if isinstance(row_index, tuple):
        payload = ",".join(str(item) for item in row_index)
    elif isinstance(row_index, list):
        payload = ",".join(str(item) for item in row_index)
    else:
        payload = str(row_index)
    return f"{family.name}[{payload}]"
