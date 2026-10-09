"""Shared prompt snippets for patch-planning agents."""

from __future__ import annotations

from framework.core import PatchOp


_OP_GUIDANCE: dict[PatchOp, str] = {
    PatchOp.UPDATE_PARAMETER: (
        "  - UPDATE_PARAMETER: use for scalar or keyed model data that already exists as a parameter; "
        "do not use it to add new rules or retune top-level objective weights. "
        "For keyed parameters, put the concrete sub-index in update.key and emit one explicit patch per keyed entry. "
        "Use update.delta for additive changes like 'increase by' or 'decrease by'; use update.value only for absolute replacement.\n"
    ),
    PatchOp.UPDATE_BOUND: (
        "  - UPDATE_BOUND: use to change the lower or upper bound of an existing variable family at a "
        "concrete index.\n"
    ),
    PatchOp.UPDATE_CONSTRAINT_RHS: (
        "  - UPDATE_CONSTRAINT_RHS: use only when changing the RHS of an existing named constraint family "
        "already present in the representation; do not invent new family names, and use a concrete existing row index. "
        "Use update.delta for additive RHS changes and update.value for absolute replacement.\n"
    ),
    PatchOp.UPDATE_OBJECTIVE_COEFF: (
        "  - UPDATE_OBJECTIVE_COEFF: use to change an indexed objective coefficient for an existing term, "
        "pair, triplet, or arc. Use update.delta for additive coefficient changes and update.value for absolute replacement.\n"
    ),
    PatchOp.UPDATE_OBJECTIVE_WEIGHT: (
        "  - UPDATE_OBJECTIVE_WEIGHT: use for top-level objective weights or penalty weights that scale a "
        "named objective component. Use update.delta for additive weight changes and update.weight for absolute replacement.\n"
    ),
    PatchOp.UPDATE_CONSTRAINT_LHS: (
        "  - UPDATE_CONSTRAINT_LHS: use only when replacing the left-hand side of an existing named "
        "constraint family; prefer ADD_CONSTRAINT_FAMILY for new rules. The replacement lhs_spec may be either "
        "a fully materialized row payload or a compact problem-specific semantic lhs_spec.kind when the representation "
        "explicitly exposes that semantic family.\n"
    ),
    PatchOp.ADD_CONSTRAINT_FAMILY: (
        "  - ADD_CONSTRAINT_FAMILY: use to add a new linear policy rule or aggregate constraint family; "
        "either provide fully materialized executable rows, or use a compact problem-specific semantic lhs_spec.kind "
        "when the representation explicitly exposes one. For compact semantic lhs_spec.kind payloads, follow the "
        "problem representation's documented field names and place semantic arguments directly under lhs_spec "
        "unless the representation explicitly defines a row-local schema. Every lhs row id must also appear in "
        "rhs_spec. For materialized rows, every term index must be a concrete executable variable index rather "
        "than a placeholder, feature name, or pseudo-code token.\n"
    ),
    PatchOp.REMOVE_CONSTRAINT_FAMILY: (
        "  - REMOVE_CONSTRAINT_FAMILY: use to remove an existing named constraint family entirely.\n"
    ),
    PatchOp.ADD_OBJECTIVE_COMPONENT: (
        "  - ADD_OBJECTIVE_COMPONENT: use to add a new named objective component, not to retune an existing "
        "weight.\n"
    ),
    PatchOp.ADD_VARIABLE_FAMILY: (
        "  - ADD_VARIABLE_FAMILY: use only when the requested change truly introduces a new decision-variable "
        "family.\n"
    ),
    PatchOp.FIX_VARIABLES_BY_PATTERN: (
        "  - FIX_VARIABLES_BY_PATTERN: use for LP-backed regex-based fixes over many variables when names are "
        "matched by pattern and optional capture-group filters. Prefer capture groups plus scope.filters for "
        "exact matches, inequalities, and interval logic instead of encoding those comparisons entirely inside "
        "the regex. Supported filter keys are group_<N> for exact match, group_<N>_in for membership, "
        "group_<N>_lte and group_<N>_gte for numeric comparisons, and range_contains_<N>_<M> for testing "
        "whether a concrete integer lies within the captured inclusive interval [group N, group M]. Capture-group "
        "indices are 1-based in left-to-right regex order.\n"
    ),
    PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN: (
        "  - UPDATE_CONSTRAINT_RHS_BY_PATTERN: use for LP-backed regex-based RHS changes across existing "
        "constraints matched by pattern; do not guess unmatched family names.\n"
    ),
    PatchOp.UPDATE_COEFFICIENT: (
        "  - UPDATE_COEFFICIENT: use for LP-backed matrix edits identified by variable and constraint regex "
        "patterns.\n"
    ),
}


_OP_SCHEMAS: dict[PatchOp, str] = {
    PatchOp.UPDATE_CONSTRAINT_RHS: (
        "  - UPDATE_CONSTRAINT_RHS -> target={'constraint': <family_name>}, "
        "update={'index': <idx>, 'value': <float>} OR {'index': <idx>, 'delta': <float>}.\n"
    ),
    PatchOp.UPDATE_PARAMETER: (
        "  - UPDATE_PARAMETER -> target={'name': <parameter_name>}, "
        "update={'name': <parameter_name>, 'value': <any>, 'key': <entity?>} OR "
        "{'name': <parameter_name>, 'delta': <float>, 'key': <entity?>}.\n"
    ),
    PatchOp.UPDATE_OBJECTIVE_WEIGHT: (
        "  - UPDATE_OBJECTIVE_WEIGHT -> target={'objective': <name>}, "
        "update={'weight': <float>} OR {'delta': <float>}.\n"
    ),
    PatchOp.UPDATE_BOUND: (
        "  - UPDATE_BOUND -> target={'variable': <family_name>}, "
        "update={'index': <idx>, 'bound': 'upper|lower', 'value': <float>}.\n"
    ),
    PatchOp.UPDATE_OBJECTIVE_COEFF: (
        "  - UPDATE_OBJECTIVE_COEFF -> target={'objective': <name>}, "
        "update={'index': [<src>, <dst>], 'value': <float>} OR {'index': [<src>, <dst>], 'delta': <float>}.\n"
    ),
    PatchOp.UPDATE_CONSTRAINT_LHS: (
        "  - UPDATE_CONSTRAINT_LHS -> target={'constraint': <family_name>}, "
        "update={'lhs_spec': <replacement spec>} where lhs_spec may be materialized or a supported semantic kind.\n"
    ),
    PatchOp.ADD_CONSTRAINT_FAMILY: (
        "  - ADD_CONSTRAINT_FAMILY -> update={'constraint': {'name': <family_name>, "
        "'index_set': [...], 'lhs_spec': {'kind': <lhs_kind>, ...}, "
        "'rhs_spec': <float or {row_idx: float}>, 'sense': '<=|=|>='}}.\n"
    ),
    PatchOp.REMOVE_CONSTRAINT_FAMILY: (
        "  - REMOVE_CONSTRAINT_FAMILY -> target={'constraint': <family_name>}.\n"
    ),
    PatchOp.ADD_OBJECTIVE_COMPONENT: (
        "  - ADD_OBJECTIVE_COMPONENT -> update={'objective': <ObjectiveComponent-like payload>}.\n"
    ),
    PatchOp.ADD_VARIABLE_FAMILY: (
        "  - ADD_VARIABLE_FAMILY -> update={'variable_family': <VariableFamily-like payload>}.\n"
    ),
    PatchOp.FIX_VARIABLES_BY_PATTERN: (
        "  - FIX_VARIABLES_BY_PATTERN -> target={'pattern': <regex>}, "
        "scope={'filters': {<group_N_op>: <val>, ...}}, update={'lb': <float>, 'ub': <float>}. "
        "Available filters: group_<N>, group_<N>_in, group_<N>_lte, group_<N>_gte, "
        "range_contains_<N>_<M>.\n"
    ),
    PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN: (
        "  - UPDATE_CONSTRAINT_RHS_BY_PATTERN -> target={'pattern': <regex>}, "
        "update={'value': <float>} OR update={'factor': <float>}.\n"
    ),
    PatchOp.UPDATE_COEFFICIENT: (
        "  - UPDATE_COEFFICIENT -> target={'variable_pattern': <regex>, "
        "'constraint_pattern': <regex>}, update={'delta': <float>} OR update={'value': <float>}.\n"
    ),
}


def build_operator_guidance(ops: list[PatchOp]) -> str:
    return "".join(_OP_GUIDANCE.get(op, "") for op in ops)


def build_schema_lines(ops: list[PatchOp]) -> str:
    return "".join(_OP_SCHEMAS.get(op, "") for op in ops)
