"""Transport-specific patch normalization helpers."""

from __future__ import annotations

from typing import Iterable, List

from framework.core import Patch, PatchOp, StructuredEvent, StructuredModel


def normalize_transport_patches(
    patches: Iterable[Patch],
    model: StructuredModel,
    event: StructuredEvent,
) -> List[Patch]:
    plants = set(model.parameters.get("plants", []))
    customers = set(model.parameters.get("customers", []))
    normalized: List[Patch] = []
    for patch in patches:
        normalized.append(_normalize_patch(patch, plants, customers, model, event))
    return normalized


def _normalize_patch(
    patch: Patch,
    plants: set[str],
    customers: set[str],
    model: StructuredModel,
    event: StructuredEvent,
) -> Patch:
    if patch.op == PatchOp.UPDATE_CONSTRAINT_RHS:
        constraint_name = patch.target.get("constraint")
        if constraint_name in model.variables:
            idx = _coerce_route_index(patch, event, plants, customers)
            value = patch.update.get("value")
            return Patch(
                op=PatchOp.UPDATE_BOUND,
                target={"variable": constraint_name},
                scope=patch.scope,
                update={"index": idx, "bound": "upper", "value": value},
            )

        if constraint_name in {"supply_constraints", "demand_constraints"}:
            idx = _normalize_index_for_rhs(patch.update.get("index"), plants, customers)
            idx = _canonicalize_entity(idx, plants, customers)
            param_name = "supply" if constraint_name == "supply_constraints" else "demand"
            value = patch.update.get("value")
            update = {"name": param_name, "key": idx, "value": value}
            if param_name == "demand":
                update["value"] = _maybe_additive_value(value, model, idx, event)
            return Patch(op=PatchOp.UPDATE_PARAMETER, target={"name": param_name}, scope=patch.scope, update=update)

    if patch.op == PatchOp.UPDATE_PARAMETER:
        update = dict(patch.update)
        key = _canonicalize_entity(update.get("key"), plants, customers)
        update["key"] = key
        update["name"] = update.get("name") or patch.target.get("name")
        if update.get("name") == "demand" and key in model.parameters.get("demand", {}):
            update["value"] = _maybe_additive_value(update.get("value"), model, key, event)
        return Patch(op=patch.op, target=patch.target, scope=patch.scope, update=update)

    if patch.op == PatchOp.UPDATE_BOUND:
        update = dict(patch.update)
        update["index"] = _coerce_bound_index(patch, event, plants, customers)
        return Patch(op=patch.op, target=patch.target, scope=patch.scope, update=update)

    return patch


def _normalize_index_for_rhs(index: object, plants: set[str], customers: set[str]) -> object:
    if isinstance(index, (list, tuple)):
        if not index:
            return index
        if len(index) == 1:
            return index[0]
        for item in index:
            candidate = _canonicalize_entity(item, plants, customers)
            if candidate in plants or candidate in customers:
                return candidate
        return index[0]
    return index


def _coerce_route_index(
    patch: Patch,
    event: StructuredEvent,
    plants: set[str],
    customers: set[str],
) -> tuple[str, str] | None:
    raw_index = patch.update.get("index")
    if raw_index is None:
        raw_index = patch.scope.get("entity")
    idx = _canonicalize_index(raw_index, plants, customers)
    if isinstance(idx, tuple) and len(idx) == 2:
        return idx

    plant = _extract_from_sets(event.affected_sets, {"plant", "plants", "Plant"})
    customer = _extract_from_sets(event.affected_sets, {"customer", "customers", "Customer"})
    if plant and customer:
        return _canonicalize_entity(plant, plants, customers), _canonicalize_entity(customer, plants, customers)

    if isinstance(idx, str):
        if idx in plants or idx in customers:
            other = patch.scope.get("entity")
            other = _canonicalize_entity(other, plants, customers)
            if idx in plants and other in customers:
                return idx, other
            if idx in customers and other in plants:
                return other, idx
    return None


def _extract_from_sets(affected_sets: dict, keys: set[str]) -> object | None:
    for key in keys:
        if key in affected_sets:
            value = affected_sets[key]
            if isinstance(value, list) and value:
                return value[0]
            return value
    return None


def _canonicalize_index(index: object, plants: set[str], customers: set[str]) -> object:
    if isinstance(index, (list, tuple)):
        collapsed = _collapse_singleton(index)
        if collapsed is not index:
            return _canonicalize_index(collapsed, plants, customers)
        return tuple(_canonicalize_entity(item, plants, customers) for item in index)
    if isinstance(index, str):
        route = _parse_route_text(index)
        if route is not None:
            src, dst = route
            return _canonicalize_entity(src, plants, customers), _canonicalize_entity(dst, plants, customers)
        if "->" in index:
            src, dst = (_clean_token(part) for part in index.split("->", 1))
            return _canonicalize_entity(src, plants, customers), _canonicalize_entity(dst, plants, customers)
        if "," in index:
            src, dst = (_clean_token(part) for part in index.split(",", 1))
            return _canonicalize_entity(src, plants, customers), _canonicalize_entity(dst, plants, customers)
    return _canonicalize_entity(index, plants, customers)


def _canonicalize_entity(value: object, plants: set[str], customers: set[str]) -> object:
    if isinstance(value, (list, tuple)):
        collapsed = _collapse_singleton(value)
        if collapsed is not value:
            return _canonicalize_entity(collapsed, plants, customers)
        return tuple(_canonicalize_entity(item, plants, customers) for item in value)
    if not isinstance(value, str):
        return value
    value = _clean_token(value)
    if value in plants or value in customers:
        return value
    lowered = value.lower()
    if lowered.startswith("plant "):
        suffix = lowered.replace("plant ", "").strip()
        candidate = f"P{suffix}"
        return candidate if candidate in plants else value
    if lowered.startswith("plant_"):
        suffix = lowered.replace("plant_", "").strip()
        candidate = f"P{suffix}"
        return candidate if candidate in plants else value
    if lowered.startswith("customer "):
        suffix = lowered.replace("customer ", "").strip()
        candidate = f"C{suffix}"
        return candidate if candidate in customers else value
    if lowered.startswith("customer_"):
        suffix = lowered.replace("customer_", "").strip()
        candidate = f"C{suffix}"
        return candidate if candidate in customers else value
    return value


def _clean_token(value: str) -> str:
    return value.strip().strip("()[]{}")


def _collapse_singleton(value: object) -> object:
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return value[0]
    return value


def _parse_route_text(value: str) -> tuple[str, str] | None:
    lowered = value.strip().lower()
    if lowered.startswith("flows"):
        text = value
        if "[" in text and "]" in text:
            text = text[text.find("[") + 1 : text.rfind("]")]
        elif "(" in text and ")" in text:
            text = text[text.find("(") + 1 : text.rfind(")")]
        if "," in text:
            src, dst = (_clean_token(part) for part in text.split(",", 1))
            return src, dst
    return None


def _coerce_bound_index(
    patch: Patch,
    event: StructuredEvent,
    plants: set[str],
    customers: set[str],
) -> object:
    raw_index = patch.update.get("index")
    if isinstance(raw_index, (int, float)):
        raw_index = None
    idx = _canonicalize_index(raw_index, plants, customers)
    if isinstance(idx, tuple) and len(idx) == 2:
        return idx

    scope_entity = patch.scope.get("entity") or patch.scope.get("plant")
    if isinstance(scope_entity, str):
        route = _parse_route_text(scope_entity)
        if route is not None:
            src, dst = route
            return _canonicalize_entity(src, plants, customers), _canonicalize_entity(dst, plants, customers)
    plant = _canonicalize_entity(scope_entity, plants, customers)
    customer = _canonicalize_entity(patch.scope.get("customer"), plants, customers)
    if not plant:
        plant = _canonicalize_entity(_extract_from_sets(event.affected_sets, {"plant", "plants", "Plant"}), plants, customers)
    if not customer:
        customer = _canonicalize_entity(_extract_from_sets(event.affected_sets, {"customer", "customers", "Customer"}), plants, customers)

    if isinstance(idx, str):
        if idx in plants and customer in customers:
            return idx, customer
        if idx in customers and plant in plants:
            return plant, idx

    if plant in plants and customer in customers:
        return plant, customer

    return idx


def _maybe_additive_value(value: object, model: StructuredModel, key: str, event: StructuredEvent) -> object:
    if not isinstance(value, (int, float)):
        return value
    text = (event.raw_text or "").lower()
    if "additional" in text or "increase by" in text or "add " in text:
        current = model.parameters.get("demand", {}).get(key)
        if isinstance(current, (int, float)):
            return current + float(value)
    return value
