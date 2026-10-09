"""Paper-facing failure taxonomy helpers."""

from __future__ import annotations

from typing import Any


_PATCHEDIT_VISIBLE = {
    "patch_application_failed": ("Patch Validation", "Invalid patch application"),
    "no_incumbent": ("Patched Solve", "No incumbent after patch"),
    "solve_failed": ("Patched Solve", "Patched model failed to solve"),
}

_CODEEDIT_VISIBLE = {
    "planner_backend_failed": ("Edit Generation", "Planner backend failed"),
    "edit_format_degraded": ("Edit Generation", "Malformed edit output"),
    "no_edit": ("Edit Generation", "No material edit"),
    "no_model_effective_edit": ("Edit Generation", "No model-effective edit"),
    "invalid_workspace_payload": ("Edit Generation", "Invalid edit workspace"),
    "compile_failed": ("Reload / Build", "Edited code failed to compile"),
    "reload_failed": ("Reload / Build", "Edited code failed to reload"),
    "build_failed": ("Reload / Build", "Edited model build failed"),
    "no_incumbent": ("Edited Solve", "No incumbent after edit"),
    "solve_failed": ("Edited Solve", "Edited model failed to solve"),
}


def attach_patchedit_failure(
    exc: BaseException,
    *,
    kind: str,
    detail: str | None = None,
) -> BaseException:
    setattr(exc, "patchedit_failure_kind", kind)
    setattr(exc, "patchedit_failure_detail", detail or str(exc))
    return exc


def success_failure_summary(
    *,
    strategy_selection: Any | None = None,
) -> dict[str, Any]:
    return {
        "failure_stage": None,
        "failure_label": None,
        "failure_detail": None,
        "selection_fallback_used": bool(getattr(strategy_selection, "fallback_used", False)),
    }


def classify_visible_failure(
    exc: Exception,
    *,
    planner_output: Any | None = None,
    strategy_selection: Any | None = None,
    planner_mode: str | None = None,
) -> dict[str, Any]:
    planner_output = planner_output if planner_output is not None else getattr(exc, "reopt_planner_output", None)
    strategy_selection = (
        strategy_selection
        if strategy_selection is not None
        else getattr(exc, "reopt_strategy_selection", None)
    )
    planner_mode = planner_mode if planner_mode is not None else getattr(exc, "reopt_planner_mode", None)
    detail = str(
        getattr(exc, "codeedit_failure_message", None)
        or getattr(exc, "patchedit_failure_detail", None)
        or exc
    )
    selection_fallback_used = bool(getattr(strategy_selection, "fallback_used", False))

    if _is_codeedit_failure(exc, planner_output=planner_output, planner_mode=planner_mode):
        stage, label = _classify_codeedit(exc)
    else:
        stage, label = _classify_patchedit(exc, planner_output=planner_output)

    return {
        "failure_stage": stage,
        "failure_label": label,
        "failure_detail": detail,
        "selection_fallback_used": selection_fallback_used,
    }


def _is_codeedit_failure(
    exc: Exception,
    *,
    planner_output: Any | None,
    planner_mode: str | None,
) -> bool:
    if getattr(exc, "codeedit_failure_kind", None):
        return True
    action_kind = str(getattr(planner_output, "action_kind", "") or "").strip().lower()
    if action_kind == "codeedit":
        return True
    return str(planner_mode or "").strip().lower() == "codeedit"


def _classify_codeedit(exc: Exception) -> tuple[str, str]:
    kind = str(getattr(exc, "codeedit_failure_kind", "") or "").strip()
    if kind:
        return _CODEEDIT_VISIBLE.get(kind, _CODEEDIT_VISIBLE["planner_backend_failed"])
    return _CODEEDIT_VISIBLE["planner_backend_failed"]


def _classify_patchedit(
    exc: Exception,
    *,
    planner_output: Any | None,
) -> tuple[str, str]:
    if planner_output is None:
        return "Interpretation", "Planner backend failed"

    hints = dict(getattr(planner_output, "planning_hints", {}) or {})
    if bool(hints.get("planner_parse_fallback")):
        return "Interpretation", "Unparseable patch plan"

    output_executable = hints.get("planner_output_executable")
    if output_executable is None:
        output_executable = bool(
            list(getattr(planner_output, "candidate_action_sets", []) or [])
            or list(getattr(planner_output, "candidate_actions", []) or [])
        )
    if not output_executable:
        return "Interpretation", "No executable patch"

    kind = str(getattr(exc, "patchedit_failure_kind", "") or "").strip()
    if kind:
        return _PATCHEDIT_VISIBLE.get(kind, _PATCHEDIT_VISIBLE["solve_failed"])

    if str(exc).startswith("No incumbent solution available"):
        return _PATCHEDIT_VISIBLE["no_incumbent"]
    return _PATCHEDIT_VISIBLE["solve_failed"]
