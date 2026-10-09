"""Utilities for tuning LP instances with Gurobi."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gurobipy as gp


@dataclass(frozen=True)
class TunePaths:
    """Artifact locations for one tuned LP instance."""

    lp_path: Path
    log_path: Path
    summary_path: Path
    param_prefix: Path


def lp_artifact_stem(lp_path: str | Path) -> str:
    """Return a stable artifact stem for an LP path.

    Bundled instances often use a generic ``model.lp`` filename. In that case,
    use the parent directory name to avoid artifact collisions.
    """

    lp = Path(lp_path).expanduser().resolve()
    if lp.name == "model.lp" and lp.parent.name:
        return lp.parent.name
    return lp.stem


def discover_lp_files(lp_dir: str | Path, pattern: str = "*.lp") -> list[Path]:
    """Return sorted LP files directly under the given directory."""

    root = Path(lp_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"LP directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"LP directory is not a directory: {root}")
    return sorted(path for path in root.glob(pattern) if path.is_file())


def select_lp_files(
    lp_files: list[Path],
    *,
    lp_path: str | Path | None = None,
    index: int | None = None,
) -> list[Path]:
    """Select either one LP file or the full discovered list."""

    if lp_path is not None and index is not None:
        raise ValueError("Pass either lp_path or index, not both")

    if lp_path is not None:
        selected = Path(lp_path).expanduser().resolve()
        if not selected.exists():
            raise FileNotFoundError(f"LP file not found: {selected}")
        if not selected.is_file():
            raise ValueError(f"LP path is not a file: {selected}")
        return [selected]

    if index is not None:
        if index < 0 or index >= len(lp_files):
            raise IndexError(f"LP index {index} out of range for {len(lp_files)} files")
        return [lp_files[index]]

    return list(lp_files)


def build_tune_paths(output_root: str | Path, lp_path: str | Path) -> TunePaths:
    """Build output paths for one LP file."""

    root = Path(output_root).expanduser().resolve()
    lp = Path(lp_path).expanduser().resolve()
    stem = lp_artifact_stem(lp)

    log_dir = root / "logs"
    summary_dir = root / "summary"
    param_dir = root / "params"
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    param_dir.mkdir(parents=True, exist_ok=True)

    return TunePaths(
        lp_path=lp,
        log_path=log_dir / f"{stem}.log",
        summary_path=summary_dir / f"{stem}.json",
        param_prefix=param_dir / stem,
    )


def load_prm_params(
    prm_path: str | Path,
    *,
    ignore_names: set[str] | None = None,
) -> dict[str, Any]:
    """Parse a Gurobi .prm file into a parameter dictionary."""

    path = Path(prm_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PRM file not found: {path}")
    if not path.is_file():
        raise ValueError(f"PRM path is not a file: {path}")

    ignored = {"LicenseID"}
    if ignore_names:
        ignored.update(ignore_names)

    params: dict[str, Any] = {}
    with path.open('r', encoding='utf-8') as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            name, raw_value = parts
            if name in ignored:
                continue
            params[name] = _parse_prm_value(raw_value)
    return _recover_params_from_adjacent_log(path, params)


def _recover_params_from_adjacent_log(prm_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    log_path = _default_log_path_for_prm(prm_path)
    if log_path is None or not log_path.exists():
        return params

    recovered_sets = _extract_improved_param_sets(log_path)
    rank = _param_rank_from_path(prm_path)
    if rank >= len(recovered_sets):
        return params

    recovered = recovered_sets[rank]
    if not recovered or recovered == params:
        return params

    _write_param_file(prm_path, recovered)
    return recovered


def _default_log_path_for_prm(prm_path: Path) -> Path | None:
    base_stem = re.sub(r"\.rank\d+$", "", prm_path.stem)
    if prm_path.parent.name == "params":
        return prm_path.parent.parent / "logs" / f"{base_stem}.log"
    return prm_path.with_name(f"{base_stem}.log")


def _param_rank_from_path(prm_path: Path) -> int:
    match = re.search(r"\.rank(\d+)$", prm_path.stem)
    if match is None:
        return 0
    return max(int(match.group(1)) - 1, 0)


def _extract_improved_param_sets(log_path: Path) -> list[dict[str, Any]]:
    sets: dict[int, dict[str, Any]] = {}
    current_rank: int | None = None
    current_params: dict[str, Any] | None = None

    for raw_line in log_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        stripped = raw_line.strip()
        match = re.match(r"Improved parameter set (\d+) ", stripped)
        if match is not None:
            if current_rank is not None and current_params is not None:
                sets[current_rank] = dict(current_params)
            current_rank = int(match.group(1)) - 1
            current_params = {}
            continue

        if current_rank is None:
            continue
        if not stripped:
            continue
        if stripped.startswith("# Name"):
            sets[current_rank] = dict(current_params or {})
            current_rank = None
            current_params = None
            continue

        parts = stripped.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        if not name[:1].isalpha():
            continue
        if current_params is None:
            current_params = {}
        current_params[name] = _parse_prm_value(parts[1])

    if current_rank is not None and current_params is not None:
        sets[current_rank] = dict(current_params)

    return [sets[idx] for idx in sorted(sets)]


def _write_param_file(path: Path, params: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        for name, value in params.items():
            fh.write(f"{name}  {_format_prm_value(value)}\n")


def _format_prm_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def tune_lp_instance(
    lp_path: str | Path,
    *,
    output_root: str | Path,
    threads: int = 8,
    mip_gap: float = 1e-2,
    tune_time_limit: float = 86_400,
    tune_results: int = 1,
    tune_trials: int | None = None,
) -> dict[str, Any]:
    """Tune one LP instance and persist logs, summaries, and .prm files."""

    paths = build_tune_paths(output_root, lp_path)
    summary: dict[str, Any] = {
        "lp_path": str(paths.lp_path),
        "log_path": str(paths.log_path),
        "summary_path": str(paths.summary_path),
        "threads": int(threads),
        "mip_gap": float(mip_gap),
        "tune_time_limit": float(tune_time_limit),
        "tune_results_requested": int(tune_results),
        "tune_trials": int(tune_trials) if tune_trials is not None else None,
        "status": "started",
        "param_files": [],
    }

    try:
        model = gp.read(str(paths.lp_path))
        model.Params.LogFile = str(paths.log_path)
        model.Params.Threads = int(threads)
        model.Params.MIPGap = float(mip_gap)
        model.Params.TuneTimeLimit = float(tune_time_limit)
        model.Params.TuneResults = int(tune_results)
        if tune_trials is not None:
            model.Params.TuneTrials = int(tune_trials)

        model.tune()
        result_count = _get_tune_result_count(model)
        summary["tune_result_count"] = result_count
        summary["status"] = "ok"

        recovered_param_sets = _extract_improved_param_sets(paths.log_path)
        param_files: list[str] = []
        param_sources: list[str] = []
        for idx in range(result_count):
            model.getTuneResult(idx)
            prm_path = _param_file_for_rank(paths.param_prefix, idx)
            model.write(str(prm_path))
            if idx < len(recovered_param_sets) and recovered_param_sets[idx]:
                _write_param_file(prm_path, recovered_param_sets[idx])
                param_sources.append("log_recovery")
            else:
                param_sources.append("model_write")
            param_files.append(str(prm_path))
        summary["param_files"] = param_files
        summary["param_sources"] = param_sources
    except Exception as exc:
        summary["status"] = "error"
        summary["error"] = str(exc)
        raise
    finally:
        _write_summary(paths.summary_path, summary)

    return summary


def tune_lp_batch(
    lp_files: list[Path],
    *,
    output_root: str | Path,
    threads: int = 8,
    mip_gap: float = 1e-2,
    tune_time_limit: float = 86_400,
    tune_results: int = 1,
    tune_trials: int | None = None,
) -> list[dict[str, Any]]:
    """Tune a sequence of LP files, continuing past per-instance failures."""

    summaries: list[dict[str, Any]] = []
    for lp_path in lp_files:
        try:
            summary = tune_lp_instance(
                lp_path,
                output_root=output_root,
                threads=threads,
                mip_gap=mip_gap,
                tune_time_limit=tune_time_limit,
                tune_results=tune_results,
                tune_trials=tune_trials,
            )
        except Exception:
            summary = _load_summary(build_tune_paths(output_root, lp_path).summary_path)
        summaries.append(summary)
    return summaries


def _parse_prm_value(raw_value: str) -> Any:
    text = raw_value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _param_file_for_rank(param_prefix: Path, rank: int) -> Path:
    if rank == 0:
        return param_prefix.with_suffix('.prm')
    return param_prefix.parent / f"{param_prefix.name}.rank{rank + 1}.prm"


def _get_tune_result_count(model: Any) -> int:
    for attr in ("TuneResultCount", "tuneResultCount"):
        value = getattr(model, attr, None)
        if value is not None:
            return int(value)
    return 0


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)


def _load_summary(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)
