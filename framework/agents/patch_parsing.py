"""Shared helpers for parsing patch-planner JSON outputs."""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable

from .prompt_utils import coerce_patch_op, fix_leading_zero_numbers, strip_markdown_fences
from framework.core import Patch, PatchOp


PatchNormalizer = Callable[
    [PatchOp, dict[str, Any], dict[str, Any], dict[str, Any]],
    tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
]


def parse_json_object(raw: str, *, label: str) -> dict[str, Any]:
    cleaned = strip_markdown_fences(raw)
    cleaned = fix_leading_zero_numbers(cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        try:
            data = ast.literal_eval(_normalize_python_literal_fallback(cleaned))
        except (SyntaxError, ValueError) as fallback_exc:
            raise RuntimeError(f"{label} could not parse JSON: {raw}") from fallback_exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} expected a JSON object")
    return data


_LINE_COMMENT_RE = re.compile(r"//.*?$", flags=re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", flags=re.DOTALL)
_JSON_TRUE_RE = re.compile(r'(?<![\w"])true(?![\w"])')
_JSON_FALSE_RE = re.compile(r'(?<![\w"])false(?![\w"])')
_JSON_NULL_RE = re.compile(r'(?<![\w"])null(?![\w"])')


def _normalize_python_literal_fallback(text: str) -> str:
    normalized = _BLOCK_COMMENT_RE.sub("", text)
    normalized = _LINE_COMMENT_RE.sub("", normalized)
    normalized = _JSON_TRUE_RE.sub("True", normalized)
    normalized = _JSON_FALSE_RE.sub("False", normalized)
    normalized = _JSON_NULL_RE.sub("None", normalized)
    return normalized


def parse_patch_list(
    raw_patches: list[Any],
    *,
    normalize_patch_fields: PatchNormalizer,
    allow_wrapped: bool = False,
    allow_inferred_op: bool = False,
    merge_extra_fields: bool = False,
) -> list[Patch]:
    patches: list[Patch] = []
    for patch_data in raw_patches:
        if not isinstance(patch_data, dict):
            continue
        patch_payload = dict(patch_data)
        if allow_wrapped:
            patch_payload = unwrap_patch_wrapper(patch_payload)
        target = patch_payload.get("target") or {}
        scope = patch_payload.get("scope") or {}
        update = patch_payload.get("update") or {}
        inferred_op = patch_payload.get("op") or patch_payload.get("type") or patch_payload.get("operator")
        if inferred_op is None and allow_inferred_op:
            inferred_op = infer_patch_op(target, update)
        op = coerce_patch_op(inferred_op)
        if not isinstance(target, dict):
            target = {}
        if not isinstance(scope, dict):
            scope = {}
        if not isinstance(update, dict):
            update = {}
        if merge_extra_fields:
            extra_fields = {
                key: value
                for key, value in patch_payload.items()
                if key not in {"op", "type", "operator", "target", "scope", "update", "notes"}
            }
            if extra_fields:
                merged_update = dict(extra_fields)
                merged_update.update(update)
                update = merged_update
        target, scope, update = normalize_patch_fields(op, target, scope, update)
        patches.append(
            Patch(
                op=op,
                target=target,
                scope=scope,
                update=update,
                notes=str(patch_payload.get("notes") or ""),
            )
        )
    return patches


def extract_from_scope(scope: dict[str, Any], keys: list[str], *, collapse_list: bool = False) -> Any:
    for key in keys:
        if key not in scope:
            continue
        value = scope[key]
        if collapse_list and isinstance(value, list) and value:
            return value[0]
        return value
    return None


def _non_null_update_field(update: dict[str, Any], key: str) -> Any | None:
    value = update.get(key)
    if value is None:
        return None
    return value


def normalize_constraint_rhs_fields(
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
    *,
    scope_keys: list[str],
    collapse_scope_list: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_update = dict(update)
    delta = _non_null_update_field(raw_update, "delta")
    constraint = (
        target.get("constraint")
        or target.get("constraint_id")
        or update.get("constraint")
        or update.get("constraint_id")
    )
    if not constraint:
        raise RuntimeError("UPDATE_CONSTRAINT_RHS requires target['constraint']")
    index = update.get("index")
    if index is None:
        index = extract_from_scope(scope, scope_keys, collapse_list=collapse_scope_list)
    elif isinstance(index, (int, float)) and "entity" in scope:
        index = scope["entity"]
    if index is None:
        raise RuntimeError("UPDATE_CONSTRAINT_RHS requires update['index']")
    value = update.get("value")
    if value is None:
        value = update.get("rhs")
    if value is None and delta is None:
        raise RuntimeError("UPDATE_CONSTRAINT_RHS requires update['value'] or update['delta']")
    next_update: dict[str, Any] = {"index": index}
    if value is not None:
        next_update["value"] = float(value)
    if delta is not None:
        next_update["delta"] = float(delta)
    return {"constraint": constraint}, scope, next_update


def normalize_update_parameter_fields(
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
    *,
    scope_keys: list[str],
    coerce_value: Callable[[str, Any], Any],
    require_value: bool,
    preserve_delta: bool = False,
    collapse_scope_list: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_update = dict(update)
    delta = _non_null_update_field(raw_update, "delta")
    name = target.get("name") or target.get("parameter") or update.get("name") or update.get("parameter")
    if not name:
        raise RuntimeError("UPDATE_PARAMETER requires parameter name")
    value = update.get("value")
    if require_value and value is None and delta is None:
        raise RuntimeError("UPDATE_PARAMETER requires update['value']")
    if not require_value and preserve_delta and value is None and delta is None:
        raise RuntimeError("UPDATE_PARAMETER requires update['value'] or update['delta']")
    key = (
        update.get("key")
        or update.get("entity")
        or target.get("key")
        or target.get("entity")
    )
    if key is None:
        key = extract_from_scope(scope, scope_keys, collapse_list=collapse_scope_list)
    coerced_value = coerce_value(str(name), value) if value is not None else None
    next_update: dict[str, Any] = {"name": name}
    if key is not None:
        next_update["key"] = key
        scope = {"entity": key}
    if value is not None:
        next_update["value"] = coerced_value
    if preserve_delta and delta is not None:
        next_update["delta"] = float(delta)
    return {"name": name}, scope, next_update


def normalize_update_bound_fields(
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
    *,
    scope_keys: list[str],
    default_variable: str,
    strict_bound_type: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    var_name = target.get("variable") or target.get("variable_family") or default_variable
    index = update.get("index")
    if index is None:
        index = extract_from_scope(scope, scope_keys, collapse_list=False)
    if index is None:
        raise RuntimeError("UPDATE_BOUND requires update['index']")
    bound_type = str(update.get("bound", "upper")).lower()
    if bound_type not in {"upper", "lower"}:
        if strict_bound_type:
            raise RuntimeError("UPDATE_BOUND requires bound=upper|lower")
        bound_type = "upper"
    value = update.get("value")
    if value is None:
        raise RuntimeError("UPDATE_BOUND requires update['value']")
    return {"variable": var_name}, scope, {"index": index, "bound": bound_type, "value": float(value)}


def normalize_update_objective_coeff_fields(
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
    *,
    objective_keys: list[str],
    default_objective: str | None = None,
    scope_keys: list[str],
    require_index: bool,
    parse_index: Callable[[Any], Any] | None = None,
    index_error_message: str = "UPDATE_OBJECTIVE_COEFF requires update['index']",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_update = dict(update)
    delta = _non_null_update_field(raw_update, "delta")
    obj_name = None
    for key in objective_keys:
        obj_name = target.get(key)
        if obj_name is not None:
            break
    if obj_name is None:
        obj_name = default_objective
    if obj_name is None:
        raise RuntimeError("UPDATE_OBJECTIVE_COEFF requires objective name")
    index = update.get("index")
    if index is None:
        index = extract_from_scope(scope, scope_keys, collapse_list=False)
    if parse_index is not None and index is not None:
        index = parse_index(index)
    if require_index and index is None:
        raise RuntimeError(index_error_message)
    value = update.get("value")
    if value is None and delta is None:
        raise RuntimeError("UPDATE_OBJECTIVE_COEFF requires update['value'] or update['delta']")
    next_update: dict[str, Any] = {"index": index}
    if value is not None:
        next_update["value"] = float(value)
    if delta is not None:
        next_update["delta"] = float(delta)
    return {"objective": obj_name}, scope, next_update


def normalize_update_objective_weight_fields(
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
    *,
    coerce_value: Callable[[Any], Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_update = dict(update)
    delta = _non_null_update_field(raw_update, "delta")
    obj_name = target.get("objective") or target.get("objective_name") or target.get("parameter")
    if obj_name is None:
        obj_name = update.get("objective") or update.get("name") or update.get("parameter")
    if obj_name is None:
        raise RuntimeError("UPDATE_OBJECTIVE_WEIGHT requires target['objective']")
    weight = update.get("weight")
    if weight is None:
        weight = update.get("value")
    if weight is None and delta is None:
        raise RuntimeError("UPDATE_OBJECTIVE_WEIGHT requires update['weight'] or update['delta']")
    next_update: dict[str, Any] = {}
    if weight is not None:
        next_update["weight"] = coerce_value(weight)
    if delta is not None:
        next_update["delta"] = float(delta)
    return {"objective": obj_name}, scope, next_update


def normalize_add_constraint_family_fields(
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    constraint = update.get("constraint")
    if constraint is None and looks_like_constraint_mapping(update):
        constraint = dict(update)
    if constraint is None and looks_like_constraint_mapping(target):
        constraint = dict(target)
        target = {}
    if constraint is None:
        raise RuntimeError("ADD_CONSTRAINT_FAMILY requires update['constraint']")
    return target, scope, {"constraint": _normalize_constraint_mapping(constraint)}


def unwrap_patch_wrapper(patch_data: dict[str, Any]) -> dict[str, Any]:
    if "op" in patch_data:
        return patch_data
    if len(patch_data) != 1:
        return patch_data
    wrapper_key, wrapper_payload = next(iter(patch_data.items()))
    if not isinstance(wrapper_payload, dict):
        return patch_data
    try:
        op = coerce_patch_op_from_wrapper_key(str(wrapper_key))
    except RuntimeError:
        return patch_data
    normalized = dict(wrapper_payload)
    normalized.setdefault("op", op.value)
    return normalized


def coerce_patch_op_from_wrapper_key(raw: str) -> PatchOp:
    normalized = raw.strip().replace("-", "_").upper()
    for candidate in PatchOp:
        if candidate.value == normalized:
            return candidate
    raise RuntimeError(f"Unsupported patch wrapper '{raw}'")


def infer_patch_op(target: dict[str, Any], update: dict[str, Any]) -> str | None:
    if not isinstance(target, dict) or not isinstance(update, dict):
        return None
    if "constraint" in update and looks_like_constraint_mapping(update.get("constraint") or {}):
        return PatchOp.ADD_CONSTRAINT_FAMILY.value
    if target.get("constraint"):
        if "constraint" in update and looks_like_constraint_mapping(update.get("constraint") or {}):
            return PatchOp.ADD_CONSTRAINT_FAMILY.value
        if any(key in update for key in {"index", "value", "rhs"}):
            return PatchOp.UPDATE_CONSTRAINT_RHS.value
    if target.get("objective"):
        if any(key in update for key in {"weight", "value"}):
            return PatchOp.UPDATE_OBJECTIVE_WEIGHT.value
        if "index" in update:
            return PatchOp.UPDATE_OBJECTIVE_COEFF.value
    if target.get("variable") or target.get("variable_family"):
        if any(key in update for key in {"bound", "value"}):
            return PatchOp.UPDATE_BOUND.value
    if target.get("name") or target.get("parameter"):
        return PatchOp.UPDATE_PARAMETER.value
    return None


def looks_like_constraint_mapping(payload: dict[str, Any]) -> bool:
    return bool(payload) and "name" in payload and (
        "lhs_spec" in payload or "rhs_spec" in payload or "index_set" in payload
    )


def coerce_numeric_or_preserve(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _normalize_constraint_mapping(constraint: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(constraint)
    index_set = normalized.get("index_set")
    if isinstance(index_set, list):
        normalized["index_set"] = [_coerce_numeric_identifier(item) for item in index_set]

    lhs_spec = normalized.get("lhs_spec")
    if isinstance(lhs_spec, dict):
        lhs_spec = dict(lhs_spec)
        lhs_kind = str(lhs_spec.get("kind") or "").strip().lower()
        rows = lhs_spec.get("rows")
        if isinstance(rows, dict):
            lhs_spec["rows"] = {
                _coerce_numeric_identifier(row_key): _normalize_constraint_row_payload(lhs_kind, row_payload)
                for row_key, row_payload in rows.items()
            }
        normalized["lhs_spec"] = lhs_spec

    rhs_spec = normalized.get("rhs_spec")
    if isinstance(rhs_spec, dict):
        normalized["rhs_spec"] = {
            _coerce_numeric_identifier(row_key): rhs_value
            for row_key, rhs_value in rhs_spec.items()
        }
    return normalized


def _normalize_constraint_term(term: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(term)
    index = normalized.get("index")
    if isinstance(index, list):
        normalized["index"] = [_coerce_numeric_identifier(item) for item in index]
    else:
        normalized["index"] = _coerce_numeric_identifier(index)
    return normalized


def _coerce_numeric_identifier(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped == "0":
        return 0
    if stripped.isdigit() and not stripped.startswith("0"):
        return int(stripped)
    return value


def _normalize_constraint_row_payload(lhs_kind: str, row_payload: Any) -> Any:
    if lhs_kind in {"materialized_linear", "materialized_linear_family"} and isinstance(row_payload, list):
        return [
            _normalize_constraint_term(term) if isinstance(term, dict) else term
            for term in row_payload
        ]
    return _normalize_semantic_payload(row_payload)


def _normalize_semantic_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_semantic_payload(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[Any, Any] = {}
        for key, payload in value.items():
            if key == "slots" and isinstance(payload, list):
                normalized[key] = [_coerce_numeric_identifier(item) for item in payload]
                continue
            if key == "fixed_block":
                normalized[key] = _coerce_numeric_scalar(payload)
                continue
            if key == "block_weights" and isinstance(payload, dict):
                normalized[key] = {
                    _coerce_numeric_identifier(block): _coerce_numeric_scalar(weight)
                    for block, weight in payload.items()
                }
                continue
            normalized[key] = _normalize_semantic_payload(payload)
        return normalized
    return _coerce_numeric_scalar(value)


def _coerce_numeric_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped == "0":
        return 0
    if stripped.isdigit() and not stripped.startswith("0"):
        return int(stripped)
    try:
        return float(stripped)
    except ValueError:
        return value
