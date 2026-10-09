"""Projection helpers for exam codeedit heuristic support."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import re
from typing import Any

from framework.core import DeltaRequest, Patch, PatchOp, StructuredModel, apply_patch
from problems.exam_block_seq.constraint_families import (
    build_frontload_constraint_family,
    build_reserved_virtual_slot_constraint_family,
    build_slot_load_cap_constraint_family,
    canonical_early_slots,
)
from problems.exam_block_seq.patching import _penultimate_evening_slot
from problems.exam_block_seq.prompt_params import exam_prompt_params

RESERVED_VIRTUAL_SLOT_EFFECT = "reserved_virtual_slot"
FRONTLOAD_EFFECT = "frontload"
SLOT_LOAD_CAP_EFFECT = "slot_load_cap"
PAIR_COUNT_EFFECT = "pair_count_update"
OBJECTIVE_WEIGHT_EFFECT = "objective_weight_update"

PROJECTION_STATE_DEAD = "dead"
PROJECTION_STATE_PROJECTABLE = "projectable"
PROJECTION_STATE_NONPROJECTABLE = "nonprojectable"

_NO_SOLVER_DIFF_REASON = "No editable exam solver diff was produced."
_DEAD_EDIT_REASON = "Code-edit diff did not change the optimization model."
_NONPROJECTABLE_REASON = (
    "Recognized a real exam code edit, but could not fairly project it into structured heuristic semantics."
)

_MODELISH_TOKENS = (
    "addconstr",
    "addvars",
    "setobjective",
    "quicksum",
    "objective",
    "constraint",
    "reserved_slot",
    "reserved_slots",
    "early_slots",
    "slot_cutoff",
    "large_blocks",
    "slot_load_cap",
    "block_enrollment",
    "pair_counts",
    "triplet_counts",
    "weights",
    "gamma",
    "beta",
    "alpha",
    "delta",
    "rhs",
    "lhs",
    "lb",
    "ub",
)

_WEIGHT_NAMES = ("alpha", "beta", "gamma1", "gamma2", "delta")
_PAIR_KEYS = ((4, 9), (9, 4))
_NUMERIC_PATTERN = r"[-+]?\d+(?:\.\d+)?"


@dataclass(frozen=True)
class ExamCodeeditProjection:
    projection_state: str
    projection_reason: str
    model_effective_edit: bool
    heuristic_warm_start_available: bool
    projected_semantics: tuple[str, ...] = ()
    projected_patches: tuple[Patch, ...] = ()

    def as_metadata(self) -> dict[str, Any]:
        return {
            "heuristic_warm_start_available": self.heuristic_warm_start_available,
            "heuristic_warm_start_reason": self.projection_reason,
            "model_effective_edit": self.model_effective_edit,
            "model_effective_edit_reason": self.projection_reason,
            "projection_state": self.projection_state,
            "projection_reason": self.projection_reason,
            "projected_semantics": list(self.projected_semantics),
            "projected_patch_ops": [patch.op.value for patch in self.projected_patches],
        }


def classify_exam_codeedit_heuristic_support(
    *,
    structured: StructuredModel,
    delta_request: DeltaRequest,
    planner_output,
) -> dict[str, Any]:
    projection = project_exam_codeedit_heuristic_support(
        structured=structured,
        delta_request=delta_request,
        planner_output=planner_output,
    )
    return projection.as_metadata()


def project_exam_codeedit_heuristic_support(
    *,
    structured: StructuredModel,
    delta_request: DeltaRequest,
    planner_output,
) -> ExamCodeeditProjection:
    edited_files = [str(item) for item in planner_output.annotations.get("edited_files") or []]
    if "solver.py" not in {item.rsplit("/", 1)[-1] for item in edited_files}:
        return _dead_projection(_NO_SOLVER_DIFF_REASON)

    unified_diff = str(getattr(planner_output, "annotations", {}).get("unified_diff") or "")
    changed_diff = _changed_diff_text(unified_diff)
    lowered_diff = changed_diff.lower()
    if not changed_diff.strip():
        return _dead_projection(_DEAD_EDIT_REASON)

    projected_patches: list[Patch] = []
    projected_semantics: list[str] = []
    nonprojectable_reasons: list[str] = []

    if _touches_reserved_virtual_slot_mechanism(lowered_diff):
        reserved_projection = _project_reserved_virtual_slot(structured, lowered_diff)
        if reserved_projection is None:
            nonprojectable_reasons.append(
                "Detected reserved-slot-related code edits, but could not ground a reserved virtual slot assignment."
            )
        else:
            projected_patches.extend(reserved_projection)
            projected_semantics.append(RESERVED_VIRTUAL_SLOT_EFFECT)

    if _matches_frontload_effect(lowered_diff):
        frontload_projection = _project_frontload(structured, delta_request)
        if frontload_projection is None:
            nonprojectable_reasons.append(
                "Detected frontload-related code edits, but could not ground slot_cutoff_exclusive."
            )
        else:
            projected_patches.extend(frontload_projection)
            projected_semantics.append(FRONTLOAD_EFFECT)

    if _touches_slot_load_cap_mechanism(lowered_diff):
        slot_load_projection = _project_slot_load_cap(
            structured,
            delta_request=delta_request,
            lowered_diff=lowered_diff,
        )
        if slot_load_projection is None:
            nonprojectable_reasons.append(
                "Detected slot-load-cap-related code edits, but could not ground the target day slots or cap."
            )
        else:
            projected_patches.extend(slot_load_projection)
            projected_semantics.append(SLOT_LOAD_CAP_EFFECT)

    if _touches_pair_count_mechanism(lowered_diff):
        pair_count_projection = _project_pair_count_updates(structured, lowered_diff)
        if pair_count_projection is None:
            nonprojectable_reasons.append(
                "Detected pair-count-related code edits, but could not ground the effective updates on (4,9) and (9,4)."
            )
        else:
            projected_patches.extend(pair_count_projection)
            projected_semantics.append(PAIR_COUNT_EFFECT)

    if _touches_objective_weight_mechanism(lowered_diff):
        objective_projection = _project_objective_weight_updates(structured, lowered_diff)
        if objective_projection is None:
            nonprojectable_reasons.append(
                "Detected objective-weight-related code edits, but could not ground the resulting weight updates."
            )
        else:
            projected_patches.extend(objective_projection)
            projected_semantics.append(OBJECTIVE_WEIGHT_EFFECT)

    semantics = list(dict.fromkeys(projected_semantics))
    if projected_patches and not _has_unexplained_modelish_effects(lowered_diff, semantics):
        return ExamCodeeditProjection(
            projection_state=PROJECTION_STATE_PROJECTABLE,
            projection_reason=(
                "Projected codeedit into structured heuristic semantics: "
                + ", ".join(semantics)
                + "."
            ),
            model_effective_edit=True,
            heuristic_warm_start_available=True,
            projected_semantics=tuple(semantics),
            projected_patches=tuple(projected_patches),
        )

    if projected_patches:
        nonprojectable_reasons.append(
            "Recognized some exam semantics, but residual model-effective edits could not be projected fairly."
        )

    if nonprojectable_reasons:
        return ExamCodeeditProjection(
            projection_state=PROJECTION_STATE_NONPROJECTABLE,
            projection_reason=" ".join(dict.fromkeys(nonprojectable_reasons)),
            model_effective_edit=True,
            heuristic_warm_start_available=False,
        )

    if _looks_dead_edit(changed_diff):
        return _dead_projection(_DEAD_EDIT_REASON)

    return ExamCodeeditProjection(
        projection_state=PROJECTION_STATE_NONPROJECTABLE,
        projection_reason=_NONPROJECTABLE_REASON,
        model_effective_edit=True,
        heuristic_warm_start_available=False,
    )


def apply_exam_codeedit_heuristic_projection(
    structured: StructuredModel,
    *,
    projection: ExamCodeeditProjection,
) -> None:
    for patch in projection.projected_patches:
        apply_patch(structured, patch, in_place=True)


def _dead_projection(reason: str) -> ExamCodeeditProjection:
    return ExamCodeeditProjection(
        projection_state=PROJECTION_STATE_DEAD,
        projection_reason=reason,
        model_effective_edit=False,
        heuristic_warm_start_available=False,
    )


def _project_reserved_virtual_slot(
    structured: StructuredModel | None,
    lowered_diff: str,
) -> list[Patch] | None:
    if not _touches_reserved_virtual_slot_mechanism(lowered_diff):
        return None
    if structured is None:
        return [Patch(op=PatchOp.UPDATE_PARAMETER, target={"name": "reserved_slots"})]

    reserved_slots = _infer_reserved_slots_from_codeedit_diff(structured, lowered_diff)
    if not reserved_slots:
        return None
    try:
        family = build_reserved_virtual_slot_constraint_family(
            virtual_blocks=structured.parameters.get("virtual_blocks", []),
            reserved_slots=reserved_slots,
        )
    except RuntimeError:
        return None
    return [
        Patch(
            op=PatchOp.UPDATE_PARAMETER,
            target={"name": "reserved_slots"},
            update={"name": "reserved_slots", "value": reserved_slots},
        ),
        Patch(
            op=PatchOp.ADD_CONSTRAINT_FAMILY,
            target={"constraint": family.name},
            update={"constraint": family},
        ),
    ]


def _infer_reserved_slots_from_codeedit_diff(
    structured: StructuredModel,
    lowered_diff: str,
) -> list[int] | None:
    """Infer the concrete reserved slots chosen by a codeedit diff.

    This intentionally evaluates only a small expression subset over the exam
    data surface. It keeps projection tied to the slot the edited code appears
    to choose instead of silently replacing it with the reference slot.
    """

    interpreter = _ReservedSlotDiffInterpreter(structured)
    interpreter.run(lowered_diff)
    slots = interpreter.reserved_slots()
    if slots:
        return slots

    # A narrow fallback for the canonical phrasing emitted by the prompt and
    # some models. This still grounds the slot from the diff pattern; it does
    # not default every reserved_slots edit to the reference operation.
    if (
        "penultimate evening slot" in lowered_diff
        or "previous evening slot" in lowered_diff
        or "prior evening" in lowered_diff
    ) and (
        "final evening" in lowered_diff
        or "last evening" in lowered_diff
    ):
        return [int(_penultimate_evening_slot(structured))]
    return None


class _ReservedSlotDiffInterpreter:
    def __init__(self, structured: StructuredModel) -> None:
        blocks = [int(block) for block in structured.parameters.get("blocks", [])]
        slot_times = [str(slot_time).lower() for slot_time in structured.parameters.get("slot_times", [])]
        slots_per_day = int(structured.parameters.get("slots_per_day", 0) or 0)
        data = {
            "blocks": list(blocks),
            "slot_times": list(slot_times),
            "slots_per_day": slots_per_day,
            "reserved_slots": [],
        }
        self.env: dict[str, Any] = {
            "data": data,
            "runtime_data": data,
            "blocks": list(blocks),
            "block_ids": list(blocks),
            "blocks_list": list(blocks),
            "slots": list(blocks),
            "slot_times": list(slot_times),
            "slots_per_day": slots_per_day,
            "reserved_slots": [],
        }
        self._initial_reserved_slots: set[int] = set()
        self._reserved_slot_candidates: set[int] = set()

    def run(self, diff_text: str) -> None:
        active_stack: list[tuple[int, bool]] = []
        for indent, line in _logical_code_lines(diff_text):
            while active_stack and indent <= active_stack[-1][0]:
                active_stack.pop()
            parent_active = all(active for _, active in active_stack)

            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("if ") and stripped.endswith(":"):
                condition = stripped[3:-1].strip()
                active_stack.append((indent, parent_active and bool(self._eval_expr(condition))))
                continue
            if not parent_active:
                continue

            self._execute_line(stripped)

    def reserved_slots(self) -> list[int]:
        candidates: set[int] = set(self._reserved_slot_candidates)
        for name, value in self.env.items():
            if _is_reserved_slot_value_name(name):
                candidates.update(_flatten_ints(value))
        data = self.env.get("data")
        if isinstance(data, dict):
            candidates.update(_flatten_ints(data.get("reserved_slots")))
        candidates.difference_update(self._initial_reserved_slots)
        return sorted(slot for slot in candidates if slot > 0)

    def _execute_line(self, line: str) -> None:
        if line.startswith(("for ", "else:", "elif ", "return ", "with ")):
            return
        if match := re.match(r"(?P<name>[a-z_][a-z0-9_]*)\s*:\s*[^=]+=\s*(?P<expr>.+)$", line):
            self.env[match.group("name")] = self._eval_expr(match.group("expr"))
            return
        if match := re.match(r"(?P<name>[a-z_][a-z0-9_]*)\s*=\s*(?P<expr>.+)$", line):
            self.env[match.group("name")] = self._eval_expr(match.group("expr"))
            return
        if match := re.match(r"data\[[\"']reserved_slots[\"']\]\s*=\s*(?P<expr>.+)$", line):
            data = self.env.get("data")
            if isinstance(data, dict):
                data["reserved_slots"] = self._eval_expr(match.group("expr"))
                self._reserved_slot_candidates.update(_flatten_ints(data["reserved_slots"]))
            return
        if match := re.match(
            r"(?P<name>[a-z_][a-z0-9_]*)\.(?P<method>add|append)\((?P<expr>.+)\)$",
            line,
        ):
            target = self.env.get(match.group("name"))
            value = self._eval_expr(match.group("expr"))
            if isinstance(value, int):
                if isinstance(target, set):
                    target.add(value)
                    if "reserved" in match.group("name"):
                        self._reserved_slot_candidates.add(value)
                elif isinstance(target, list):
                    target.append(value)
                    if "reserved" in match.group("name"):
                        self._reserved_slot_candidates.add(value)

    def _eval_expr(self, expr: str) -> Any:
        try:
            node = ast.parse(expr.strip(), mode="eval")
        except SyntaxError:
            return None
        try:
            return self._eval_node(node.body)
        except Exception:
            return None

    def _eval_node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value.lower()
            return node.value
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.List):
            return [self._eval_node(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._eval_node(item) for item in node.elts)
        if isinstance(node, ast.Set):
            return {self._eval_node(item) for item in node.elts}
        if isinstance(node, ast.BinOp):
            return _safe_binary_op(type(node.op), self._eval_node(node.left), self._eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = self._eval_node(node.operand)
            return -value if isinstance(value, (int, float)) else None
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = None
                for value_node in node.values:
                    result = self._eval_node(value_node)
                    if not result:
                        return result
                return result
            if isinstance(node.op, ast.Or):
                result = None
                for value_node in node.values:
                    result = self._eval_node(value_node)
                    if result:
                        return result
                return result
        if isinstance(node, ast.Compare):
            return self._eval_compare(node)
        if isinstance(node, ast.Subscript):
            value = self._eval_node(node.value)
            index = self._eval_slice(node.slice)
            return value[index]
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.ListComp):
            return self._eval_list_comp(node)
        if isinstance(node, ast.SetComp):
            values = self._eval_list_comp(
                ast.ListComp(elt=node.elt, generators=node.generators),
            )
            return set(values) if values is not None else None
        if isinstance(node, ast.Attribute):
            value = self._eval_node(node.value)
            return getattr(value, node.attr)
        return None

    def _eval_slice(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Slice):
            return slice(
                self._eval_node(node.lower) if node.lower is not None else None,
                self._eval_node(node.upper) if node.upper is not None else None,
                self._eval_node(node.step) if node.step is not None else None,
            )
        return self._eval_node(node)

    def _eval_compare(self, node: ast.Compare) -> bool:
        left = self._eval_node(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self._eval_node(comparator)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            elif isinstance(op, ast.Is):
                ok = left is right
            elif isinstance(op, ast.IsNot):
                ok = left is not right
            else:
                return False
            if not ok:
                return False
            left = right
        return True

    def _eval_call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Name):
            args = [self._eval_node(arg) for arg in node.args]
            func = node.func.id
            if func == "int":
                return int(args[0])
            if func == "float":
                return float(args[0])
            if func == "len":
                return len(args[0])
            if func == "range":
                return range(*[int(arg) for arg in args])
            if func == "enumerate":
                return enumerate(args[0])
            if func == "zip":
                return zip(*args)
            if func == "list":
                return list(args[0]) if args else []
            if func == "set":
                return set(args[0]) if args else set()
            if func == "sorted":
                return sorted(args[0])
            if func == "max":
                return max(args[0])
            if func == "min":
                return min(args[0])
        if isinstance(node.func, ast.Attribute):
            owner = self._eval_node(node.func.value)
            args = [self._eval_node(arg) for arg in node.args]
            if node.func.attr == "get" and isinstance(owner, dict):
                default = args[1] if len(args) > 1 else None
                return owner.get(args[0], default)
            if node.func.attr == "lower" and isinstance(owner, str):
                return owner.lower()
            if node.func.attr == "strip" and isinstance(owner, str):
                return owner.strip()
            if node.func.attr == "index" and isinstance(owner, list):
                return owner.index(args[0])
        return None

    def _eval_list_comp(self, node: ast.ListComp) -> list[Any] | None:
        if len(node.generators) != 1:
            return None
        generator = node.generators[0]
        iterable = self._eval_node(generator.iter)
        if iterable is None:
            return None
        result: list[Any] = []
        previous_env = dict(self.env)
        for item in iterable:
            self._assign_target(generator.target, item)
            if all(bool(self._eval_node(condition)) for condition in generator.ifs):
                result.append(self._eval_node(node.elt))
        self.env = previous_env
        return result

    def _assign_target(self, target: ast.AST, value: Any) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
            return
        if isinstance(target, ast.Tuple) and isinstance(value, (list, tuple)):
            for child, child_value in zip(target.elts, value):
                self._assign_target(child, child_value)


def _logical_code_lines(diff_text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    pending_indent: int | None = None
    pending_parts: list[str] = []
    bracket_balance = 0
    for raw_line in diff_text.splitlines():
        code = raw_line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip())
        stripped = code.strip()
        if pending_parts:
            pending_parts.append(stripped)
            bracket_balance += _bracket_delta(stripped)
            if bracket_balance <= 0:
                lines.append((int(pending_indent or 0), " ".join(pending_parts)))
                pending_indent = None
                pending_parts = []
            continue
        bracket_balance = _bracket_delta(stripped)
        if bracket_balance > 0 and "=" in stripped:
            pending_indent = indent
            pending_parts = [stripped]
            continue
        lines.append((indent, stripped))
    if pending_parts:
        lines.append((int(pending_indent or 0), " ".join(pending_parts)))
    return lines


def _bracket_delta(line: str) -> int:
    return sum(line.count(opening) - line.count(closing) for opening, closing in (("[", "]"), ("(", ")"), ("{", "}")))


def _safe_binary_op(op_type: type[ast.operator], left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    if op_type is ast.Add:
        return left + right
    if op_type is ast.Sub:
        return left - right
    if op_type is ast.Mult:
        return left * right
    if op_type is ast.FloorDiv:
        return left // right
    if op_type is ast.Div:
        return left / right
    if op_type is ast.Mod:
        return left % right
    return None


def _flatten_ints(value: Any) -> set[int]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, float) and value.is_integer():
        return {int(value)}
    if isinstance(value, (list, tuple, set)):
        result: set[int] = set()
        for item in value:
            result.update(_flatten_ints(item))
        return result
    return set()


def _is_reserved_slot_value_name(name: str) -> bool:
    if name in {"reserved_slots", "reserved_slot_values", "reserved_slot_prev_evening"}:
        return True
    if name in {"reserved_index"} or name.endswith("_index") or name.endswith("_idx"):
        return False
    return bool(re.search(r"\b(?:reserve|reserved)_slot", name))


def _project_frontload(
    structured: StructuredModel,
    delta_request: DeltaRequest,
) -> list[Patch] | None:
    prompt_params = _resolve_prompt_params(delta_request, structured)
    cutoff_exclusive = prompt_params.get("slot_cutoff_exclusive")
    if not isinstance(cutoff_exclusive, (int, float)):
        return None

    early_slots = canonical_early_slots(
        structured.parameters.get("blocks", []),
        int(cutoff_exclusive),
    )
    family = build_frontload_constraint_family(
        large_blocks=structured.parameters.get("large_blocks", []),
        early_slots=early_slots,
    )
    return [
        Patch(
            op=PatchOp.UPDATE_PARAMETER,
            target={"name": "early_slots"},
            update={"name": "early_slots", "value": early_slots},
        ),
        Patch(
            op=PatchOp.UPDATE_CONSTRAINT_LHS,
            target={"constraint": "frontload"},
            update={"lhs_spec": family.lhs_spec},
        ),
    ]


def _project_slot_load_cap(
    structured: StructuredModel,
    *,
    delta_request: DeltaRequest,
    lowered_diff: str,
) -> list[Patch] | None:
    grounded = _ground_slot_load_cap(structured, delta_request=delta_request, lowered_diff=lowered_diff)
    if grounded is None:
        return None

    family = build_slot_load_cap_constraint_family(
        row_name=grounded["row_name"],
        slots=grounded["slots"],
        real_blocks=grounded["real_blocks"],
        block_enrollment=grounded["block_enrollment"],
        cap=grounded["cap"],
    )
    return [
        Patch(
            op=PatchOp.ADD_CONSTRAINT_FAMILY,
            target={"constraint": family.name},
            update={"constraint": family},
        )
    ]


def _project_pair_count_updates(
    structured: StructuredModel,
    lowered_diff: str,
) -> list[Patch] | None:
    current_values = {
        pair: _effective_pair_count(structured, pair)
        for pair in _PAIR_KEYS
    }
    projected: list[Patch] = []

    for pair in _PAIR_KEYS:
        updated = _apply_pair_update_sequence(lowered_diff, pair=pair, current_value=current_values[pair])
        if updated is None:
            continue
        projected.append(
            Patch(
                op=PatchOp.UPDATE_PARAMETER,
                target={"name": "pair_counts"},
                scope={"entity": list(pair)},
                update={"name": "p", "key": list(pair), "value": updated},
            )
        )

    return projected or None


def _project_objective_weight_updates(
    structured: StructuredModel,
    lowered_diff: str,
) -> list[Patch] | None:
    base_weights = {
        name: float(structured.objectives.get(name).weight if structured.objectives.get(name) is not None else 0.0)
        for name in _WEIGHT_NAMES
    }
    current_weights = dict(base_weights)
    changed: set[str] = set()

    for raw_line in lowered_diff.splitlines():
        line = raw_line.strip().lower()
        if not line:
            continue
        for name in _WEIGHT_NAMES:
            resolved = _resolve_weight_update_from_line(
                line,
                weight_name=name,
                current_weights=current_weights,
                base_weights=base_weights,
            )
            if resolved is None:
                continue
            current_weights[name] = resolved
            changed.add(name)

    if not changed:
        return None

    return [
        Patch(
            op=PatchOp.UPDATE_OBJECTIVE_WEIGHT,
            target={"objective": name},
            update={"weight": float(current_weights[name])},
        )
        for name in _WEIGHT_NAMES
        if name in changed
    ]


def _ground_slot_load_cap(
    structured: StructuredModel,
    *,
    delta_request: DeltaRequest,
    lowered_diff: str,
) -> dict[str, Any] | None:
    raw_text = str(delta_request.text or "").lower()
    cap_match = re.search(r"\b4000(?:\.0+)?\b", lowered_diff)
    day_two_requested = (
        "day_2" in lowered_diff
        or "day 2" in lowered_diff
        or ("day 2" in raw_text and "4000" in raw_text)
    )
    if not day_two_requested and cap_match is None:
        return None

    slots_per_day = int(structured.parameters.get("slots_per_day", 0) or 0)
    if slots_per_day <= 0:
        return None
    day_two_slots = list(range(slots_per_day + 1, (2 * slots_per_day) + 1))
    if not day_two_slots:
        return None

    virtual_blocks = {int(block) for block in structured.parameters.get("virtual_blocks", [])}
    real_blocks = [
        int(block)
        for block in structured.parameters.get("blocks", [])
        if int(block) not in virtual_blocks
    ]
    return {
        "row_name": "day_2",
        "slots": day_two_slots,
        "real_blocks": real_blocks,
        "block_enrollment": dict(structured.parameters.get("block_enrollment") or {}),
        "cap": 4000.0,
    }


def _matches_frontload_effect(lowered_diff: str) -> bool:
    if "early_slots" not in lowered_diff:
        return False
    return any(
        token in lowered_diff
        for token in ("large_blocks", "frontload", "slot_cutoff", "cutoff_exclusive")
    )


def _touches_reserved_virtual_slot_mechanism(lowered_diff: str) -> bool:
    return "reserved_slot" in lowered_diff or "reserved_slots" in lowered_diff


def _touches_slot_load_cap_mechanism(lowered_diff: str) -> bool:
    if "slot_load_cap" in lowered_diff or "block_enrollment" in lowered_diff:
        return True
    return bool(re.search(r"\bcap\s*=", lowered_diff))


def _touches_pair_count_mechanism(lowered_diff: str) -> bool:
    return "pair_counts" in lowered_diff or "key_4_9" in lowered_diff or "key_9_4" in lowered_diff


def _touches_objective_weight_mechanism(lowered_diff: str) -> bool:
    if "weights[" in lowered_diff:
        return True
    return any(re.search(rf"\b{name}\b", lowered_diff) for name in _WEIGHT_NAMES)


def _has_unexplained_modelish_effects(lowered_diff: str, semantics: list[str]) -> bool:
    recognized = set(semantics)
    checks = (
        (RESERVED_VIRTUAL_SLOT_EFFECT, _touches_reserved_virtual_slot_mechanism),
        (FRONTLOAD_EFFECT, _matches_frontload_effect),
        (SLOT_LOAD_CAP_EFFECT, _touches_slot_load_cap_mechanism),
        (PAIR_COUNT_EFFECT, _touches_pair_count_mechanism),
        (OBJECTIVE_WEIGHT_EFFECT, _touches_objective_weight_mechanism),
    )
    for effect, predicate in checks:
        if effect in recognized:
            continue
        if predicate(lowered_diff):
            return True
    return False


def _apply_pair_update_sequence(
    lowered_diff: str,
    *,
    pair: tuple[int, int],
    current_value: float,
) -> float | None:
    pair_tokens = {
        f"({pair[0]}, {pair[1]})",
        f"({pair[0]},{pair[1]})",
        f"key_{pair[0]}_{pair[1]}",
    }
    value = float(current_value)
    changed = False

    for raw_line in lowered_diff.splitlines():
        line = raw_line.strip().lower()
        if "pair_counts" not in line and "p[" not in line:
            continue
        if not any(token in line for token in pair_tokens):
            continue

        if match := re.search(rf"\+=\s*({_NUMERIC_PATTERN})", line):
            value += float(match.group(1))
            changed = True
            continue
        if match := re.search(rf"\*=\s*({_NUMERIC_PATTERN})", line):
            value *= float(match.group(1))
            changed = True
            continue
        if match := re.search(rf"\+\s*({_NUMERIC_PATTERN})", line):
            value += float(match.group(1))
            changed = True
            continue
        if match := re.search(rf"=\s*({_NUMERIC_PATTERN})\s*$", line):
            value = float(match.group(1))
            changed = True

    return value if changed else None


def _effective_pair_count(structured: StructuredModel, pair: tuple[int, int]) -> float:
    overrides = dict(structured.parameters.get("p") or {})
    if pair in overrides and isinstance(overrides[pair], (int, float)):
        return float(overrides[pair])
    base_values = dict(structured.parameters.get("pair_counts") or {})
    if pair in base_values and isinstance(base_values[pair], (int, float)):
        return float(base_values[pair])
    return 0.0


def _resolve_weight_update_from_line(
    line: str,
    *,
    weight_name: str,
    current_weights: dict[str, float],
    base_weights: dict[str, float],
) -> float | None:
    target_patterns = (
        rf"\b{weight_name}\b",
        rf'weights\["{weight_name}"\]',
        rf"weights\['{weight_name}'\]",
    )
    if not any(re.search(pattern, line) for pattern in target_patterns):
        return None

    if match := re.search(
        rf"(?:\b{weight_name}\b|weights\[[\"']{weight_name}[\"']\])\s*\*=\s*({_NUMERIC_PATTERN})",
        line,
    ):
        return current_weights[weight_name] * float(match.group(1))
    if match := re.search(
        rf"(?:\b{weight_name}\b|weights\[[\"']{weight_name}[\"']\])\s*\+=\s*({_NUMERIC_PATTERN})",
        line,
    ):
        return current_weights[weight_name] + float(match.group(1))
    if match := re.search(
        rf"(?:\b{weight_name}\b|weights\[[\"']{weight_name}[\"']\])\s*-=\s*({_NUMERIC_PATTERN})",
        line,
    ):
        return current_weights[weight_name] - float(match.group(1))

    assign_match = re.search(
        rf"(?:\b{weight_name}\b|weights\[[\"']{weight_name}[\"']\])\s*=\s*(?P<expr>.+)$",
        line,
    )
    if assign_match is None:
        return None
    expr = assign_match.group("expr").strip()
    return _resolve_weight_expression(expr, current_weights=current_weights, base_weights=base_weights)


def _resolve_weight_expression(
    expr: str,
    *,
    current_weights: dict[str, float],
    base_weights: dict[str, float],
) -> float | None:
    expr = expr.strip().rstrip(",")
    resolved = _resolve_weight_source(expr, current_weights=current_weights, base_weights=base_weights)
    if resolved is not None:
        return resolved

    patterns = (
        (rf"(?P<lhs>.+?)\+\s*(?P<num>{_NUMERIC_PATTERN})$", lambda left, num: left + float(num)),
        (rf"(?P<lhs>.+?)\-\s*(?P<num>{_NUMERIC_PATTERN})$", lambda left, num: left - float(num)),
        (rf"(?P<lhs>.+?)\*\s*(?P<num>{_NUMERIC_PATTERN})$", lambda left, num: left * float(num)),
        (rf"(?P<num>{_NUMERIC_PATTERN})\s*\*\s*(?P<rhs>.+)$", lambda right, num: float(num) * right),
    )
    for pattern, combine in patterns:
        match = re.match(pattern, expr)
        if match is None:
            continue
        if "lhs" in match.groupdict():
            left_value = _resolve_weight_source(
                match.group("lhs").strip(),
                current_weights=current_weights,
                base_weights=base_weights,
            )
            if left_value is not None:
                return combine(left_value, match.group("num"))
        if "rhs" in match.groupdict():
            right_value = _resolve_weight_source(
                match.group("rhs").strip(),
                current_weights=current_weights,
                base_weights=base_weights,
            )
            if right_value is not None:
                return combine(right_value, match.group("num"))
    return None


def _resolve_weight_source(
    expr: str,
    *,
    current_weights: dict[str, float],
    base_weights: dict[str, float],
) -> float | None:
    cleaned = expr.strip().strip("()")
    if re.fullmatch(_NUMERIC_PATTERN, cleaned):
        return float(cleaned)
    if cleaned in current_weights:
        return float(current_weights[cleaned])
    if cleaned.endswith("_base"):
        base_name = cleaned[:-5]
        if base_name in base_weights:
            return float(base_weights[base_name])

    for pattern in (
        r'weights\.get\(["\'](?P<name>\w+)["\']',
        r'float\(weights\.get\(["\'](?P<name>\w+)["\']',
    ):
        source_match = re.search(pattern, cleaned)
        if source_match is None:
            continue
        source_name = source_match.group("name")
        if source_name in current_weights:
            return float(current_weights[source_name])
    return None


def _changed_diff_text(unified_diff: str) -> str:
    lines: list[str] = []
    for line in str(unified_diff).splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
    return "\n".join(lines)


def _looks_dead_edit(changed_diff: str) -> bool:
    cleaned_lines: list[str] = []
    for raw_line in changed_diff.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        comment_split = stripped.split("#", 1)[0].strip()
        if comment_split:
            cleaned_lines.append(comment_split.lower())

    if not cleaned_lines:
        return True

    joined = "\n".join(cleaned_lines)
    return not any(token in joined for token in _MODELISH_TOKENS)


def _resolve_prompt_params(
    delta_request: DeltaRequest,
    structured: StructuredModel,
) -> dict[str, Any]:
    metadata = dict(delta_request.metadata or {})
    prompt_params = dict(metadata.get("params") or metadata.get("prompt_params") or {})
    if prompt_params:
        return prompt_params

    prompt_id = str(metadata.get("prompt_id") or "").strip().upper()
    if prompt_id == "P3":
        return exam_prompt_params(prompt_id, context=structured.parameters)
    return {}
