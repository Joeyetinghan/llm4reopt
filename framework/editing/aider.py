"""Helpers for bounded Aider-backed code editing."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from framework.core import ProblemAdapter, ProblemSpec
from framework.lp import render_inventory_markdown, render_lp_snippets_markdown, summarize_lp_model
from framework.llm.clients import is_openai_model
from framework.llm.config import (
    azure_openai_deployment_name,
    canonical_openai_chat_model_name,
    openai_reasoning_effort,
)
from framework.registry import load_problem


class AiderInvocationError(RuntimeError):
    """Raised when Aider cannot complete a requested edit."""


_AZURE_ENDPOINT = ""
_AZURE_API_VERSION = "2024-12-01-preview"
_AZURE_DEPLOYMENT_PREFIX = ""
_AIDER_SINGLE_SHOT_MAX_CHAT_HISTORY_TOKENS = 1_000_000
_RUNTIME_SNAPSHOT_FILE = "runtime_snapshot.json"
_LP_CODEEDIT_WRAPPER_FILE = "lp_codeedit_wrapper.py"
_LP_INVENTORY_FILE = "lp_inventory.md"
_LP_SNIPPETS_FILE = "lp_snippets.md"
_AIDER_ERROR_PATTERNS = (
    re.compile(r"litellm\.[A-Za-z]+Error:", re.IGNORECASE),
    re.compile(r"\bAuthenticationError:", re.IGNORECASE),
    re.compile(r"\bBadRequestError:", re.IGNORECASE),
    re.compile(r"\bPermissionDeniedError:", re.IGNORECASE),
    re.compile(r"\bAPIConnectionError:", re.IGNORECASE),
    re.compile(r"\bRateLimitError:", re.IGNORECASE),
    re.compile(r"The API provider is not able to authenticate you", re.IGNORECASE),
    re.compile(r"Incorrect API key provided", re.IGNORECASE),
)
_AIDER_DEGRADED_EDIT_PATTERNS = (
    re.compile(r"The LLM did not conform to the edit format", re.IGNORECASE),
    re.compile(r"SearchReplaceNoExactMatch", re.IGNORECASE),
    re.compile(r"Only \d+ reflections allowed", re.IGNORECASE),
)
_PATHLIKE_CONFIG_KEYS = {"instances_dir"}


@dataclass(frozen=True)
class CodeEditWorkspace:
    """Ephemeral workspace used for one bounded code-edit step."""

    source_problem_root: Path
    workspace_root: Path
    problem_root: Path
    editable_files: list[Path]
    read_only_files: list[Path]
    artifact_paths: dict[str, Path] = field(default_factory=dict)

    @property
    def editable_file_labels(self) -> list[str]:
        return [str(path.relative_to(self.problem_root)) for path in self.editable_files]

    @property
    def read_only_file_labels(self) -> list[str]:
        return [str(path.relative_to(self.problem_root)) for path in self.read_only_files]


@dataclass(frozen=True)
class AiderEditResult:
    """Captured results from one Aider edit invocation."""

    workspace: CodeEditWorkspace
    prompt: str
    changed_files: list[str]
    unified_diff: str
    stdout: str
    stderr: str
    returncode: int
    warnings: list[str] = field(default_factory=list)

    def to_action_payload(self) -> dict[str, Any]:
        return {
            "workspace_problem_root": str(self.workspace.problem_root),
            "source_problem_root": str(self.workspace.source_problem_root),
            "editable_files": list(self.workspace.editable_file_labels),
            "read_only_files": list(self.workspace.read_only_file_labels),
            "artifact_paths": {
                name: str(path)
                for name, path in self.workspace.artifact_paths.items()
            },
            "changed_files": list(self.changed_files),
            "unified_diff": self.unified_diff,
            "planner_warnings": list(self.warnings),
        }


def prepare_problem_edit_workspace(
    spec: ProblemSpec,
    *,
    trace_dir: str | Path | None = None,
) -> CodeEditWorkspace:
    """Copy the active packaged problem into a bounded edit workspace."""

    if spec.source_kind != "package":
        raise AiderInvocationError("Code-edit mode currently supports packaged problems only.")

    primary_kind = spec.codeedit_artifacts[0].source_type if spec.codeedit_artifacts else None
    if primary_kind not in {"python_builder", "python_model", "lp_file"}:
        raise AiderInvocationError(
            "Code-edit mode currently supports packaged Python-backed or LP-backed codeedit artifacts only; "
            f"got {primary_kind!r}."
        )

    source_problem_root = _active_problem_root(spec)
    workspace_root = (
        Path(trace_dir).expanduser().resolve() / "codeedit_workspace"
        if trace_dir is not None
        else _local_tmp_workspace_root(spec, source_problem_root)
    )
    package_root = workspace_root / "problems"
    package_root.mkdir(parents=True, exist_ok=True)
    source_package_root = source_problem_root.parent
    for shared_name in ("__init__.py", "base.py"):
        shared_source = source_package_root / shared_name
        if shared_source.exists():
            shutil.copy2(shared_source, package_root / shared_name)
    problem_root = workspace_root / "problems" / source_problem_root.name
    shutil.copytree(
        source_problem_root,
        problem_root,
        dirs_exist_ok=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "ground_truth"),
    )
    if primary_kind == "lp_file":
        editable_files, read_only_files, artifact_paths = _prepare_lp_codeedit_artifacts(spec, problem_root)
    else:
        editable_files = _editable_files(spec, problem_root)
        read_only_files = _read_only_files(spec, problem_root)
        artifact_paths = {}
        if not editable_files:
            raise AiderInvocationError(
                f"No editable Python files were surfaced for packaged problem '{spec.metadata.problem_id}'."
            )
    workspace = CodeEditWorkspace(
        source_problem_root=source_problem_root,
        workspace_root=workspace_root,
        problem_root=problem_root,
        editable_files=editable_files,
        read_only_files=read_only_files,
        artifact_paths=artifact_paths,
    )
    _validate_workspace_paths(workspace)
    return workspace


def run_aider_edit(
    workspace: CodeEditWorkspace,
    *,
    prompt: str,
    model_name: str | None,
    api_key: str | None,
) -> AiderEditResult:
    """Invoke Aider on the bounded editable file set and capture diffs."""

    _validate_workspace_paths(workspace)
    source_snapshot = _snapshot_source_files(workspace)
    before = {
        path.relative_to(workspace.problem_root): path.read_text(encoding="utf-8")
        for path in workspace.editable_files
    }
    env = _aider_env(model_name=model_name, api_key=api_key)
    command = _aider_command(workspace, prompt=prompt, model_name=model_name)
    completed = _run_subprocess(command, cwd=workspace.problem_root, env=env)
    _raise_for_aider_output(completed)
    _raise_if_source_files_changed(workspace, source_snapshot)

    after = {
        path.relative_to(workspace.problem_root): path.read_text(encoding="utf-8")
        for path in workspace.editable_files
    }
    changed_files = [
        str(relative_path)
        for relative_path in sorted(after)
        if before.get(relative_path, "") != after.get(relative_path, "")
    ]
    unified_diff = _build_unified_diff(before, after)
    warnings = _aider_warnings(completed)
    if warnings and not changed_files and not unified_diff.strip():
        raise AiderInvocationError(
            "Code-edit planner could not produce a valid edit: "
            + "; ".join(warnings)
        )
    return AiderEditResult(
        workspace=workspace,
        prompt=prompt,
        changed_files=changed_files,
        unified_diff=unified_diff,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        warnings=warnings,
    )


def load_problem_from_edit_workspace(
    spec: ProblemSpec,
    workspace: CodeEditWorkspace,
) -> tuple[ProblemSpec, ProblemAdapter]:
    """Reload a packaged problem from an edited workspace copy."""

    module_prefix = _module_prefix(spec.adapter_path)
    config_path = _map_config_path(
        spec.config_metadata.get("config_path"),
        source_root=workspace.source_problem_root,
        workspace_problem_root=workspace.problem_root,
    )
    config_mapping = dict(spec.config_metadata.get("config") or {})
    original_config_path = spec.config_metadata.get("config_path")
    if config_mapping and original_config_path is not None:
        config_mapping = _resolve_config_paths(
            config_mapping,
            base_dir=Path(str(original_config_path)).expanduser().resolve().parent,
        )
    lp_copy_path = workspace.artifact_paths.get("lp_copy_path")
    if lp_copy_path is not None:
        config_mapping["lp_path"] = str(lp_copy_path)
    examples_path = spec.context_bundle.example_store_path

    with _prefer_workspace_modules(workspace.workspace_root, module_prefix):
        edited_spec, edited_adapter = load_problem(
            problem_root=str(workspace.problem_root),
            config_path=str(config_path) if config_path is not None else None,
            config_mapping=config_mapping or None,
            examples_path=str(examples_path) if examples_path is not None else None,
        )

    edited_spec.config_metadata["planner_mode"] = "codeedit"
    edited_spec.config_metadata["codeedit_active_problem_root"] = str(workspace.problem_root)
    if workspace.artifact_paths:
        edited_spec.config_metadata["codeedit_workspace_artifacts"] = {
            name: str(path)
            for name, path in workspace.artifact_paths.items()
        }
        loaded_data = dict(edited_spec.config_metadata.get("loaded_data") or {})
        if lp_copy_path is not None:
            loaded_data["lp_path"] = str(lp_copy_path)
        if loaded_data:
            edited_spec.config_metadata["loaded_data"] = loaded_data
    if spec.config_metadata.get("trace_root") is not None:
        edited_spec.config_metadata["trace_root"] = spec.config_metadata["trace_root"]
    return edited_spec, edited_adapter


def _run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    if shutil.which(command[0]) is None:
        raise AiderInvocationError(
            "Aider CLI is not available on PATH. Install it before using planner_mode=codeedit."
        )
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AiderInvocationError(
            "Aider edit failed "
            f"(exit={completed.returncode}): {(completed.stderr or completed.stdout).strip()}"
        )
    return completed


def _active_problem_root(spec: ProblemSpec) -> Path:
    configured = spec.config_metadata.get("codeedit_active_problem_root")
    if configured:
        return Path(str(configured)).expanduser().resolve()
    return spec.metadata.package_root.expanduser().resolve()


def _local_tmp_workspace_root(spec: ProblemSpec, source_problem_root: Path) -> Path:
    repo_tmp_root = source_problem_root.parents[1] / "tmp" / "codeedit_workspaces"
    repo_tmp_root.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f"{spec.metadata.problem_id}_",
            dir=str(repo_tmp_root),
        )
    )


def _editable_files(spec: ProblemSpec, problem_root: Path) -> list[Path]:
    editable: set[Path] = set()
    source_root = _active_problem_root(spec)
    artifact_paths_found = False

    for artifact in spec.codeedit_artifacts:
        artifact_path = Path(artifact.path).expanduser().resolve()
        try:
            relative = artifact_path.relative_to(source_root)
        except ValueError:
            continue
        candidate = problem_root / relative
        if candidate.suffix == ".py" and candidate.exists():
            editable.add(candidate)
            artifact_paths_found = True
        for extra_path in artifact.exported_artifacts:
            extra_artifact_path = Path(extra_path).expanduser().resolve()
            try:
                extra_relative = extra_artifact_path.relative_to(source_root)
            except ValueError:
                continue
            extra_candidate = problem_root / extra_relative
            if extra_candidate.suffix == ".py" and extra_candidate.exists():
                editable.add(extra_candidate)
                artifact_paths_found = True

    if artifact_paths_found:
        return sorted(editable)

    for relative_name in ("solver.py", "builder.py", "model.py"):
        candidate = problem_root / relative_name
        if candidate.exists():
            editable.add(candidate)

    return sorted(editable)


def _read_only_files(spec: ProblemSpec, problem_root: Path) -> list[Path]:
    primary_kind = spec.codeedit_artifacts[0].source_type if spec.codeedit_artifacts else None
    if primary_kind == "lp_file":
        return []
    read_only: set[Path] = set()
    source_root = _active_problem_root(spec)
    for artifact in spec.codeedit_artifacts:
        for extra_path in artifact.read_only_artifacts:
            extra_artifact_path = Path(extra_path).expanduser().resolve()
            try:
                relative = extra_artifact_path.relative_to(source_root)
            except ValueError:
                continue
            candidate = problem_root / relative
            if candidate.exists() and candidate.is_file():
                read_only.add(candidate)
    read_only.update(_generated_read_only_files(spec, problem_root))
    return sorted(read_only)


def _generated_read_only_files(spec: ProblemSpec, problem_root: Path) -> list[Path]:
    loaded_data = spec.config_metadata.get("loaded_data")
    if not isinstance(loaded_data, dict) or not loaded_data:
        return []

    snapshot_path = problem_root / _RUNTIME_SNAPSHOT_FILE
    snapshot_path.write_text(
        json.dumps(_runtime_snapshot_payload(loaded_data), indent=2) + "\n",
        encoding="utf-8",
    )
    return [snapshot_path]


def _prepare_lp_codeedit_artifacts(
    spec: ProblemSpec,
    problem_root: Path,
) -> tuple[list[Path], list[Path], dict[str, Path]]:
    lp_source = _active_lp_path(spec)
    artifact_dir = problem_root / "_codeedit"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    lp_copy_path = artifact_dir / lp_source.name
    shutil.copy2(lp_source, lp_copy_path)

    wrapper_path = problem_root / _LP_CODEEDIT_WRAPPER_FILE
    wrapper_path.write_text(_lp_codeedit_wrapper_source(), encoding="utf-8")

    snapshot_paths = _generated_read_only_files(spec, problem_root)
    runtime_snapshot_path = snapshot_paths[0] if snapshot_paths else None

    with _load_lp_model(lp_copy_path) as model:
        inventory = summarize_lp_model(model)
    inventory_path = problem_root / _LP_INVENTORY_FILE
    inventory_path.write_text(render_inventory_markdown(inventory) + "\n", encoding="utf-8")
    snippets_path = problem_root / _LP_SNIPPETS_FILE
    snippets_path.write_text(
        render_lp_snippets_markdown(inventory.get("snippets", {})) + "\n",
        encoding="utf-8",
    )

    read_only_files = [inventory_path, snippets_path]
    if runtime_snapshot_path is not None:
        read_only_files.append(runtime_snapshot_path)

    artifact_paths = {
        "wrapper_path": wrapper_path,
        "lp_copy_path": lp_copy_path,
        "lp_inventory_path": inventory_path,
        "lp_snippets_path": snippets_path,
    }
    if runtime_snapshot_path is not None:
        artifact_paths["runtime_snapshot_path"] = runtime_snapshot_path
    return [wrapper_path], read_only_files, artifact_paths


def _runtime_snapshot_payload(runtime_data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in runtime_data.items():
        key_text = str(key)
        if _skip_runtime_snapshot_key(key_text):
            continue
        payload[key_text] = _snapshot_runtime_value(value)
    return payload


def _skip_runtime_snapshot_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in {"delta_text", "prompt_id", "prompt_params"}:
        return True
    return lowered.endswith(("_path", "_dir", "_root"))


def _snapshot_runtime_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) <= 64:
            return {str(key): _snapshot_runtime_value(item) for key, item in items}
        return {
            "count": len(items),
            "preview": {str(key): _snapshot_runtime_value(item) for key, item in items[:8]},
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if len(items) <= 64:
            return [_snapshot_runtime_value(item) for item in items]
        return {
            "count": len(items),
            "preview": [_snapshot_runtime_value(item) for item in items[:16]],
        }
    return str(value)


def _build_unified_diff(
    before: dict[Path, str],
    after: dict[Path, str],
) -> str:
    chunks: list[str] = []
    for relative_path in sorted(set(before) | set(after)):
        old_text = before.get(relative_path, "")
        new_text = after.get(relative_path, "")
        if old_text == new_text:
            continue
        diff_lines = difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=str(relative_path),
            tofile=str(relative_path),
            lineterm="",
        )
        chunks.append("\n".join(diff_lines))
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _validate_workspace_paths(workspace: CodeEditWorkspace) -> None:
    if workspace.problem_root.resolve() == workspace.source_problem_root.resolve():
        raise AiderInvocationError("Code-edit workspace must not point at the source problem root.")

    for path in [*workspace.editable_files, *workspace.read_only_files]:
        resolved = path.expanduser().resolve()
        try:
            resolved.relative_to(workspace.problem_root.resolve())
        except ValueError as exc:
            raise AiderInvocationError(
                f"Code-edit file escapes the bounded workspace: {resolved}"
            ) from exc
        try:
            resolved.relative_to(workspace.source_problem_root.resolve())
        except ValueError:
            continue
        raise AiderInvocationError(
            f"Code-edit file resolves into the source problem tree: {resolved}"
        )
    for path in workspace.artifact_paths.values():
        resolved = path.expanduser().resolve()
        try:
            resolved.relative_to(workspace.problem_root.resolve())
        except ValueError as exc:
            raise AiderInvocationError(
                f"Code-edit artifact escapes the bounded workspace: {resolved}"
            ) from exc
        try:
            resolved.relative_to(workspace.source_problem_root.resolve())
        except ValueError:
            continue
        raise AiderInvocationError(
            f"Code-edit artifact resolves into the source problem tree: {resolved}"
        )


def _snapshot_source_files(workspace: CodeEditWorkspace) -> dict[Path, str]:
    snapshot: dict[Path, str] = {}
    for relative in set(workspace.editable_file_labels + workspace.read_only_file_labels):
        source_path = workspace.source_problem_root / relative
        if source_path.exists() and source_path.is_file():
            snapshot[Path(relative)] = source_path.read_text(encoding="utf-8")
    return snapshot


def _raise_if_source_files_changed(
    workspace: CodeEditWorkspace,
    before_snapshot: dict[Path, str],
) -> None:
    for relative_path, before_text in before_snapshot.items():
        source_path = workspace.source_problem_root / relative_path
        after_text = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
        if before_text != after_text:
            raise AiderInvocationError(
                f"Source problem file changed outside the bounded workspace: {relative_path}"
            )


def _aider_env(*, model_name: str | None, api_key: str | None) -> dict[str, str]:
    env = dict(os.environ)
    resolved_model = _aider_model_name(model_name)
    if resolved_model.startswith("azure/"):
        azure_key = api_key or env.get("OPENAI_API_KEY") or env.get("AZURE_API_KEY")
        if azure_key:
            env.setdefault("AZURE_API_KEY", azure_key)
            env.setdefault("AZURE_OPENAI_API_KEY", azure_key)
        env.setdefault("AZURE_API_BASE", env.get("OPENAI_AZURE_ENDPOINT", _AZURE_ENDPOINT))
        env.setdefault("AZURE_API_VERSION", env.get("OPENAI_AZURE_API_VERSION", _AZURE_API_VERSION))
        deployment_name = azure_openai_deployment_name(
            model_name,
            deployment_prefix=os.getenv("OPENAI_AZURE_DEPLOYMENT_PREFIX", _AZURE_DEPLOYMENT_PREFIX),
        )
        if deployment_name:
            env.setdefault("OPENAI_API_DEPLOYMENT_ID", deployment_name)
    elif resolved_model.startswith("gemini/"):
        gemini_key = api_key or env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
        if gemini_key:
            env.setdefault("GEMINI_API_KEY", gemini_key)
            env.setdefault("GOOGLE_API_KEY", gemini_key)
    elif api_key:
        env.setdefault("OPENAI_API_KEY", api_key)
    return env


def _aider_command(
    workspace: CodeEditWorkspace,
    *,
    prompt: str,
    model_name: str | None,
) -> list[str]:
    resolved_model = _aider_model_name(model_name)
    command = [
        "aider",
        "--yes-always",
        "--no-pretty",
        "--no-fancy-input",
        "--no-git",
        "--map-tokens",
        "0",
        "--max-chat-history-tokens",
        str(_AIDER_SINGLE_SHOT_MAX_CHAT_HISTORY_TOKENS),
        "--no-show-model-warnings",
        "--analytics-disable",
        "--no-check-update",
        "--no-restore-chat-history",
        "--input-history-file",
        str(workspace.workspace_root / ".aider.input.history"),
        "--chat-history-file",
        str(workspace.workspace_root / ".aider.chat.history.md"),
        "--llm-history-file",
        str(workspace.workspace_root / ".aider.llm.history"),
    ]
    if resolved_model:
        command.extend(["--model", resolved_model])
    model_settings_path = _aider_model_settings_path(workspace, model_name=model_name)
    if model_settings_path is not None:
        command.extend(["--model-settings-file", str(model_settings_path)])
    reasoning_effort = openai_reasoning_effort(model_name)
    if reasoning_effort:
        command.extend(["--reasoning-effort", reasoning_effort])
    for read_only_file in workspace.read_only_file_labels:
        command.extend(["--read", read_only_file])
    command.extend(["--message", prompt, *workspace.editable_file_labels])
    return command


def _aider_provider() -> str:
    """Route OpenAI models through Azure only when an Azure endpoint is configured."""
    return "azure" if os.getenv("OPENAI_AZURE_ENDPOINT", _AZURE_ENDPOINT) else "openai"


def _aider_model_name(model_name: str | None) -> str:
    resolved = (model_name or "").strip()
    if not resolved:
        return ""
    if resolved.startswith(("azure/", "openai/", "openrouter/", "gemini/")):
        return resolved
    if is_openai_model(resolved):
        deployment_name = azure_openai_deployment_name(
            resolved,
            deployment_prefix=os.getenv("OPENAI_AZURE_DEPLOYMENT_PREFIX", _AZURE_DEPLOYMENT_PREFIX),
        )
        provider = _aider_provider()
        return f"{provider}/{deployment_name}" if deployment_name else f"{provider}/"
    if resolved.lower().startswith("gemini"):
        return f"gemini/{resolved}"
    return resolved


def _aider_model_settings_path(
    workspace: CodeEditWorkspace,
    *,
    model_name: str | None,
) -> Path | None:
    settings = _aider_model_settings(model_name)
    if not settings:
        return None
    path = workspace.workspace_root / ".aider.model.settings.json"
    path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _aider_model_settings(model_name: str | None) -> list[dict[str, Any]]:
    normalized = canonical_openai_chat_model_name(model_name)
    deployment_prefix = os.getenv("OPENAI_AZURE_DEPLOYMENT_PREFIX", _AZURE_DEPLOYMENT_PREFIX)
    deployment_name = azure_openai_deployment_name(
        model_name,
        deployment_prefix=deployment_prefix,
    )
    provider = _aider_provider()
    if normalized == "gpt-4.1":
        weak_name = azure_openai_deployment_name("gpt-4.1-mini", deployment_prefix=deployment_prefix)
        return [
            {
                "name": f"{provider}/{deployment_name}",
                "edit_format": "diff",
                "weak_model_name": f"{provider}/{weak_name}",
                "use_repo_map": True,
                "reminder": "sys",
                "editor_model_name": f"{provider}/{weak_name}",
            },
        ]
    if normalized == "gpt-4.1-mini":
        return [
            {
                "name": f"{provider}/{deployment_name}",
                "edit_format": "diff",
                "use_repo_map": True,
                "reminder": "sys",
            },
        ]
    return []


def _raise_for_aider_output(completed: subprocess.CompletedProcess[str]) -> None:
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if not combined:
        return
    for pattern in _AIDER_ERROR_PATTERNS:
        if pattern.search(combined):
            raise AiderInvocationError(pattern.search(combined).group(0))


def _aider_warnings(completed: subprocess.CompletedProcess[str]) -> list[str]:
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if not combined:
        return []
    warnings: list[str] = []
    for pattern in _AIDER_DEGRADED_EDIT_PATTERNS:
        match = pattern.search(combined)
        if match is None:
            continue
        warning = match.group(0)
        if warning not in warnings:
            warnings.append(warning)
    return warnings


def _module_prefix(adapter_path: str) -> str:
    module_name, _class_name = adapter_path.split(":", 1)
    return module_name.rsplit(".", 1)[0]


def _map_config_path(
    config_path: Any,
    *,
    source_root: Path,
    workspace_problem_root: Path,
) -> Path | None:
    if config_path in {None, ""}:
        return None
    resolved = Path(str(config_path)).expanduser().resolve()
    try:
        relative = resolved.relative_to(source_root)
    except ValueError:
        return resolved
    return workspace_problem_root / relative


def _resolve_config_paths(config: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            resolved[key] = _resolve_config_paths(value, base_dir=base_dir)
            continue
        if _is_pathlike_key(key) and isinstance(value, str) and value:
            path = Path(value).expanduser()
            if path.is_absolute():
                resolved[key] = str(path.resolve())
                continue
            base_candidate = (base_dir / path).resolve()
            resolved[key] = str(base_candidate) if base_candidate.exists() else value
            continue
        resolved[key] = value
    return resolved


def _is_pathlike_key(key: str) -> bool:
    return (
        key.endswith("_path")
        or key.endswith("_dir")
        or key.endswith("_root")
        or key in _PATHLIKE_CONFIG_KEYS
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _active_lp_path(spec: ProblemSpec) -> Path:
    loaded = spec.config_metadata.get("loaded_data") or {}
    raw_lp_path = loaded.get("lp_path")
    if raw_lp_path in {None, ""}:
        raise AiderInvocationError(
            f"Packaged LP codeedit requires loaded_data['lp_path'] for '{spec.metadata.problem_id}'."
        )
    lp_path = Path(str(raw_lp_path)).expanduser().resolve()
    if not lp_path.exists() or not lp_path.is_file():
        raise AiderInvocationError(f"Resolved LP path does not exist: {lp_path}")
    return lp_path


@contextmanager
def _load_lp_model(lp_path: Path) -> Iterator[Any]:
    import gurobipy as gp

    model = gp.read(str(lp_path))
    try:
        yield model
    finally:
        try:
            model.dispose()
        except Exception:
            pass


def _lp_codeedit_wrapper_source() -> str:
    return '''"""Thin editable wrapper around a copied LP model.

Edit only `build_codeedit_model(...)`. Keep this wrapper generic and leave the
copied `.lp` file untouched.

`build_codeedit_model(...)` should mutate and return a `gurobipy.Model`.
Do not call `model.optimize()` here, and do not inspect solution values such as
`var.X` inside the builder.
"""

from __future__ import annotations

import re
from typing import Iterable

import gurobipy as gp
from gurobipy import GRB

_REOPT_CODEEDIT_METADATA = {
    "helper_call_count": 0,
    "total_matches": 0,
    "helper_hits": {
        "fix_variables_by_pattern": [],
        "update_rhs_by_pattern": [],
        "update_coefficients": [],
    },
}


def _reset_codeedit_metadata() -> None:
    _REOPT_CODEEDIT_METADATA["helper_call_count"] = 0
    _REOPT_CODEEDIT_METADATA["total_matches"] = 0
    _REOPT_CODEEDIT_METADATA["helper_hits"] = {
        "fix_variables_by_pattern": [],
        "update_rhs_by_pattern": [],
        "update_coefficients": [],
    }


def _record_helper_hit(helper_name: str, *, selector: dict[str, str], matches: int) -> int:
    match_count = int(matches)
    _REOPT_CODEEDIT_METADATA["helper_call_count"] += 1
    _REOPT_CODEEDIT_METADATA["total_matches"] += match_count
    _REOPT_CODEEDIT_METADATA["helper_hits"].setdefault(helper_name, []).append(
        {
            **selector,
            "matches": match_count,
        }
    )
    return match_count


def _load_model(runtime_data) -> gp.Model:
    lp_path = runtime_data.get("lp_path")
    if lp_path in {None, ""}:
        raise ValueError("runtime_data['lp_path'] is required")
    return gp.read(str(lp_path))


def _apply_solver_params(model: gp.Model, solver_params) -> None:
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


def _matching_variables(model: gp.Model, pattern: str):
    regex = re.compile(pattern)
    return [var for var in model.getVars() if regex.search(var.VarName)]


def _matching_constraints(model: gp.Model, pattern: str):
    regex = re.compile(pattern)
    return [constr for constr in model.getConstrs() if regex.search(constr.ConstrName)]


def _fix_variables_by_pattern(
    model: gp.Model,
    pattern: str,
    *,
    lb: float | None = None,
    ub: float | None = None,
) -> int:
    matches = _matching_variables(model, pattern)
    for variable in matches:
        if lb is not None:
            variable.LB = float(lb)
        if ub is not None:
            variable.UB = float(ub)
    return _record_helper_hit(
        "fix_variables_by_pattern",
        selector={"pattern": pattern},
        matches=len(matches),
    )


def _update_rhs_by_pattern(
    model: gp.Model,
    pattern: str,
    *,
    value: float | None = None,
    factor: float | None = None,
) -> int:
    if value is None and factor is None:
        raise ValueError("Specify value or factor when updating RHS values.")
    matches = _matching_constraints(model, pattern)
    for constraint in matches:
        if value is not None:
            constraint.RHS = float(value)
        else:
            constraint.RHS = float(constraint.RHS) * float(factor)
    return _record_helper_hit(
        "update_rhs_by_pattern",
        selector={"pattern": pattern},
        matches=len(matches),
    )


def _update_coefficients(
    model: gp.Model,
    *,
    variable_pattern: str,
    constraint_pattern: str,
    value: float | None = None,
    delta: float | None = None,
) -> int:
    if value is None and delta is None:
        raise ValueError("Specify value or delta when updating coefficients.")
    variable_regex = re.compile(variable_pattern)
    constraint_regex = re.compile(constraint_pattern)
    updated = 0
    for constraint in model.getConstrs():
        if not constraint_regex.search(constraint.ConstrName):
            continue
        row = model.getRow(constraint)
        for idx in range(row.size()):
            variable = row.getVar(idx)
            if not variable_regex.search(variable.VarName):
                continue
            current = float(row.getCoeff(idx))
            replacement = float(value) if value is not None else current + float(delta)
            model.chgCoeff(constraint, variable, replacement)
            updated += 1
    return _record_helper_hit(
        "update_coefficients",
        selector={
            "constraint_pattern": constraint_pattern,
            "variable_pattern": variable_pattern,
        },
        matches=updated,
    )


def build_codeedit_model(runtime_data, *, solver_params=None, time_limit=None) -> gp.Model:
    # Mutate the loaded model in-place and return it. Do not solve here.
    _reset_codeedit_metadata()
    model = _load_model(runtime_data)
    _apply_solver_params(model, solver_params)
    if time_limit is None:
        time_limit = runtime_data.get("time_limit")
    if time_limit is not None:
        model.Params.TimeLimit = float(time_limit)
    mip_gap = runtime_data.get("mip_gap")
    if mip_gap is not None:
        model.Params.MIPGap = float(mip_gap)
    return model
'''


@contextmanager
def _prefer_workspace_modules(workspace_root: Path, module_prefix: str) -> Iterator[None]:
    top_level_package = module_prefix.split(".", 1)[0]
    original_sys_path = list(sys.path)
    saved_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == top_level_package
        or name == module_prefix
        or name.startswith(f"{module_prefix}.")
    }
    for name in saved_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(workspace_root))
    try:
        yield
    finally:
        sys.path[:] = original_sys_path
        for name in list(sys.modules):
            if (
                name == top_level_package
                or name == module_prefix
                or name.startswith(f"{module_prefix}.")
            ):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
