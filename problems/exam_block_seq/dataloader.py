"""Utilities to load block sequencing instances from saved artifacts."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

import numpy as np

from framework.utils import lp_artifact_stem

LP_PATTERN = re.compile(r"blockseq_n(?P<size>\d+)_slots(?P<slots>\d+)_seed(?P<seed>\d+)\.lp$")
INSTANCE_DIR_PATTERN = re.compile(
    r"blockseq_n(?P<size>\d+)_blocks(?P<blocks>\d+)_slots(?P<slots>\d+)_seed(?P<seed>\d+)$"
)

KNOWN_INSTANCES: dict[str, tuple[str, str]] = {
    "blockseq_n544_blocks20_slots24_seed42": ("I1", "Spring 2024"),
    "blockseq_n601_blocks18_slots24_seed42": ("I2", "Fall 2023"),
    "blockseq_n553_blocks17_slots25_seed42": ("I3", "Spring 2023"),
    "blockseq_n588_blocks19_slots24_seed42": ("I4", "Fall 2022"),
    "blockseq_n539_blocks16_slots24_seed42": ("I5", "Spring 2022"),
}

_WEIGHT_TO_VAR = {
    "alpha": "triple_in_day",
    "beta": "triple_in_24hr",
    "gamma1": "b2b_eveMorn",
    "gamma2": "b2b_other",
    "delta": "three_exams_four_slots",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "instance_dir": "benchmark/raw_instances/blockseq_n544_blocks20_slots24_seed42",
    "lp_path": None,
    "slots_per_day": 3,
    "slot_times": ["9am", "2pm", "7pm"],
    "real_blocks": None,
    "time_limit": 600,
    "threads": 6,
    "reserved_slots": [],
    "weights": {},
    "solution_dir": "outputs/solves/base",
    "log_dir": "outputs/solves/base",
    "tuned_param_dir": "outputs/solves/tune/params",
    "delta_text": "",
    "prompt_id": "P1",
    "prompt_params": {},
    "disable_default_warm_start": False,
}


def load_block_seq_data_from_mapping(
    config: Mapping[str, Any] | None,
    *,
    base_dir: str | Path | None = None,
) -> Dict[str, Any]:
    config_data = dict(config or {})
    merged = DEFAULT_CONFIG.copy()
    if config_data.get("lp_path") not in {None, ""} and "instance_dir" not in config_data:
        merged["instance_dir"] = None
    merged.update(config_data)

    instance_dir_value = merged.get("instance_dir")
    if instance_dir_value not in {None, ""}:
        return _load_block_seq_data_from_instance_dir(
            merged,
            instance_dir=_resolve_instance_dir(instance_dir_value, base_dir=base_dir),
            base_dir=base_dir,
        )

    lp_path = _resolve_lp_path(merged, base_dir=base_dir)
    instance = parse_lp_instance(lp_path)

    slots_per_day = int(merged.get("slots_per_day", 3))
    if slots_per_day < 1:
        raise ValueError(f"slots_per_day must be positive: {slots_per_day}")

    slot_times = merged.get("slot_times") or []
    if slot_times:
        if len(slot_times) != slots_per_day:
            raise ValueError(
                f"slot_times length must match slots_per_day ({slots_per_day}): {slot_times}"
            )
        slot_times = [str(val).strip().lower() for val in slot_times]

    slots = int(instance["slots"])
    blocks = list(range(1, slots + 1))
    real_blocks_raw = merged.get("real_blocks")
    real_blocks = slots if real_blocks_raw is None else int(real_blocks_raw)
    if real_blocks < 1 or real_blocks > slots:
        raise ValueError(f"real_blocks must be within 1..{slots}: {real_blocks}")

    reserved_slots = sorted(
        {
            int(slot)
            for slot in (merged.get("reserved_slots") or [])
            if isinstance(slot, (int, float))
        }
    )
    if reserved_slots and any(slot < 1 or slot > slots for slot in reserved_slots):
        raise ValueError(f"reserved_slots must be within 1..{slots}: {reserved_slots}")

    (
        triple_24_start,
        triple_day_start,
        eve_morn_start,
        other_b2b_start,
    ) = _compute_penalty_starts(blocks, slots_per_day)

    weights = _extract_weights_from_lp(lp_path)
    weights.update(dict(merged.get("weights") or {}))

    solution_dir = _resolve_optional_path(merged.get("solution_dir"), base_dir=base_dir)
    log_dir = _resolve_optional_path(merged.get("log_dir"), base_dir=base_dir)
    tuned_param_dir = _resolve_optional_path(merged.get("tuned_param_dir"), base_dir=base_dir)

    artifact_stem = lp_artifact_stem(lp_path)
    virtual_blocks = [int(block) for block in (merged.get("virtual_blocks") or [])]
    if not virtual_blocks:
        virtual_blocks = list(range(real_blocks + 1, slots + 1))
    return {
        "lp_path": str(lp_path),
        "lp_stem": artifact_stem,
        "artifact_stem": artifact_stem,
        "instance_id": str(instance["instance_id"]),
        "reference_semester": str(instance["reference_semester"]),
        "size": int(instance["size"]),
        "seed": int(instance["seed"]),
        "slots": slots,
        "slots_per_day": slots_per_day,
        "slot_times": slot_times,
        "blocks": blocks,
        "real_blocks": real_blocks,
        "dummy_blocks": list(range(real_blocks + 1, slots + 1)),
        "virtual_blocks": virtual_blocks,
        "large_blocks": [int(block) for block in (merged.get("large_blocks") or [])],
        "early_slots": [int(slot) for slot in (merged.get("early_slots") or [])],
        "triple_24_start": triple_24_start,
        "triple_day_start": triple_day_start,
        "eve_morn_start": eve_morn_start,
        "other_b2b_start": other_b2b_start,
        "weights": weights,
        "reserved_slots": reserved_slots,
        "block_enrollment": {
            int(key): float(value)
            for key, value in dict(merged.get("block_enrollment") or {}).items()
        },
        "block_num_exams": {
            int(key): int(value)
            for key, value in dict(merged.get("block_num_exams") or {}).items()
        },
        "pair_counts": dict(merged.get("pair_counts") or {}),
        "triplet_counts": dict(merged.get("triplet_counts") or {}),
        "frontload_block_size_cutoff": merged.get("frontload_block_size_cutoff"),
        "frontload_slot_cutoff": merged.get("frontload_slot_cutoff"),
        "time_limit": int(merged.get("time_limit", 600)),
        "threads": int(merged.get("threads", DEFAULT_CONFIG["threads"])),
        "disable_default_warm_start": bool(merged.get("disable_default_warm_start", False)),
        "delta_text": merged.get("delta_text", DEFAULT_CONFIG["delta_text"]),
        "prompt_id": merged.get("prompt_id", DEFAULT_CONFIG["prompt_id"]),
        "prompt_params": dict(merged.get("prompt_params") or DEFAULT_CONFIG["prompt_params"]),
        "solution_path": str(solution_dir / f"{artifact_stem}.sol") if solution_dir is not None else None,
        "log_path": str(log_dir / f"{artifact_stem}.log") if log_dir is not None else None,
        "tuned_param_path": (
            str(tuned_param_dir / f"{artifact_stem}.prm") if tuned_param_dir is not None else None
        ),
    }


def parse_lp_instance(lp_path: str | Path) -> Dict[str, Any]:
    path = Path(lp_path).expanduser().resolve()
    match = LP_PATTERN.match(path.name)
    lookup_key = path.stem
    if match is None and path.name == "model.lp":
        lookup_key = path.parent.name
        match = INSTANCE_DIR_PATTERN.match(lookup_key)
    if match is None:
        raise ValueError(f"Unrecognized exam LP filename: {path}")
    instance_id, semester = KNOWN_INSTANCES.get(lookup_key, (lookup_key, "unknown"))
    return {
        "lp_path": path,
        "instance_id": instance_id,
        "reference_semester": semester,
        "size": int(match.group("size")),
        "slots": int(match.group("slots")),
        "seed": int(match.group("seed")),
    }


def _load_block_seq_data_from_instance_dir(
    config: Mapping[str, Any],
    *,
    instance_dir: Path,
    base_dir: str | Path | None,
) -> Dict[str, Any]:
    manifest = _load_instance_manifest(instance_dir / "instance.json")
    block_summary = _load_block_summary(instance_dir / "block_summary.csv")
    pair_counts = _load_pair_counts(instance_dir / "pair_counts.csv")
    triplet_counts = _load_triplet_counts(instance_dir / "triplet_counts.csv")
    lp_path = instance_dir / "model.lp"
    if not lp_path.exists():
        raise FileNotFoundError(f"Exam LP file not found: {lp_path}")

    instance = parse_lp_instance(lp_path)
    blocks = [int(block) for block in manifest["all_blocks"]]
    virtual_blocks = sorted(int(block) for block in manifest.get("virtual_blocks", []))
    slots = len(blocks)
    real_blocks = config.get("real_blocks")
    if real_blocks is None:
        real_blocks = slots - len(virtual_blocks)
    real_blocks = int(real_blocks)
    if real_blocks < 1 or real_blocks > slots:
        raise ValueError(f"real_blocks must be within 1..{slots}: {real_blocks}")

    slots_per_day = int(config.get("slots_per_day", 3))
    if slots_per_day < 1:
        raise ValueError(f"slots_per_day must be positive: {slots_per_day}")

    slot_times = config.get("slot_times") or []
    if slot_times:
        if len(slot_times) != slots_per_day:
            raise ValueError(
                f"slot_times length must match slots_per_day ({slots_per_day}): {slot_times}"
            )
        slot_times = [str(val).strip().lower() for val in slot_times]

    reserved_slots = sorted(
        {
            int(slot)
            for slot in (config.get("reserved_slots") or [])
            if isinstance(slot, (int, float))
        }
    )
    if reserved_slots and any(slot < 1 or slot > slots for slot in reserved_slots):
        raise ValueError(f"reserved_slots must be within 1..{slots}: {reserved_slots}")

    parameters = dict(manifest.get("parameters") or {})
    weights = _default_weights()
    weights.update(parameters)
    weights.update(dict(config.get("weights") or {}))

    solution_dir = _resolve_optional_path(config.get("solution_dir"), base_dir=base_dir)
    log_dir = _resolve_optional_path(config.get("log_dir"), base_dir=base_dir)
    tuned_param_dir = _resolve_optional_path(config.get("tuned_param_dir"), base_dir=base_dir)
    artifact_stem = lp_artifact_stem(lp_path)

    return {
        "instance_dir": str(instance_dir),
        "instance_manifest_path": str(instance_dir / "instance.json"),
        "blockmap_path": str(instance_dir / "blockmap.csv"),
        "block_summary_path": str(instance_dir / "block_summary.csv"),
        "pair_counts_path": str(instance_dir / "pair_counts.csv"),
        "triplet_counts_path": str(instance_dir / "triplet_counts.csv"),
        "lp_path": str(lp_path),
        "lp_stem": artifact_stem,
        "artifact_stem": artifact_stem,
        "instance_id": str(instance["instance_id"]),
        "reference_semester": str(instance["reference_semester"]),
        "size": int(instance["size"]),
        "seed": int(instance["seed"]),
        "slots": slots,
        "slots_per_day": slots_per_day,
        "slot_times": slot_times,
        "blocks": blocks,
        "real_blocks": real_blocks,
        "dummy_blocks": list(virtual_blocks),
        "virtual_blocks": list(virtual_blocks),
        "large_blocks": [int(block) for block in manifest.get("large_blocks", [])],
        "early_slots": [int(slot) for slot in manifest.get("early_slots", [])],
        "triple_24_start": [int(slot) for slot in manifest.get("triple_24_start", [])],
        "triple_day_start": [int(slot) for slot in manifest.get("triple_day_start", [])],
        "eve_morn_start": [int(slot) for slot in manifest.get("eve_morn_start", [])],
        "other_b2b_start": [int(slot) for slot in manifest.get("other_b2b_start", [])],
        "weights": weights,
        "reserved_slots": reserved_slots,
        "block_enrollment": block_summary["block_enrollment"],
        "block_num_exams": block_summary["block_num_exams"],
        "pair_counts": pair_counts,
        "triplet_counts": triplet_counts,
        "frontload_block_size_cutoff": parameters.get("frontload_block_size_cutoff"),
        "frontload_slot_cutoff": parameters.get("frontload_slot_cutoff"),
        "time_limit": int(config.get("time_limit", 600)),
        "threads": int(config.get("threads", DEFAULT_CONFIG["threads"])),
        "disable_default_warm_start": bool(config.get("disable_default_warm_start", False)),
        "delta_text": config.get("delta_text", DEFAULT_CONFIG["delta_text"]),
        "prompt_id": config.get("prompt_id", DEFAULT_CONFIG["prompt_id"]),
        "prompt_params": dict(config.get("prompt_params") or DEFAULT_CONFIG["prompt_params"]),
        "solution_path": str(solution_dir / f"{artifact_stem}.sol") if solution_dir is not None else None,
        "log_path": str(log_dir / f"{artifact_stem}.log") if log_dir is not None else None,
        "tuned_param_path": (
            str(tuned_param_dir / f"{artifact_stem}.prm") if tuned_param_dir is not None else None
        ),
    }


def _load_instance_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Instance manifest not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_block_summary(path: Path) -> Dict[str, Dict[int, int]]:
    if not path.exists():
        raise FileNotFoundError(f"Block summary not found: {path}")
    block_enrollment: dict[int, int] = {}
    block_num_exams: dict[int, int] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            block = int(row["block"])
            block_enrollment[block] = int(row["block_enrollment"])
            block_num_exams[block] = int(row["num_exams"])
    return {
        "block_enrollment": block_enrollment,
        "block_num_exams": block_num_exams,
    }


def _load_pair_counts(path: Path) -> Dict[tuple[int, int], float]:
    if not path.exists():
        raise FileNotFoundError(f"Pair count file not found: {path}")
    pair_counts: Dict[tuple[int, int], float] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pair_counts[(int(row["block_i"]), int(row["block_j"]))] = float(row["count"])
    return pair_counts


def _load_triplet_counts(path: Path) -> Dict[tuple[int, int, int], float]:
    if not path.exists():
        raise FileNotFoundError(f"Triplet count file not found: {path}")
    triplet_counts: Dict[tuple[int, int, int], float] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            triplet_counts[(int(row["block_i"]), int(row["block_j"]), int(row["block_k"]))] = float(row["count"])
    return triplet_counts


def _resolve_instance_dir(path_value: str | Path, *, base_dir: str | Path | None) -> Path:
    instance_dir = _resolve_path(path_value, base_dir=base_dir)
    if not instance_dir.exists():
        raise FileNotFoundError(f"Exam instance directory not found: {instance_dir}")
    if not instance_dir.is_dir():
        raise NotADirectoryError(f"Exam instance path is not a directory: {instance_dir}")
    return instance_dir


def _resolve_lp_path(config: Mapping[str, Any], *, base_dir: str | Path | None) -> Path:
    lp_value = config.get("lp_path")
    if not lp_value:
        raise ValueError("'lp_path' must point to an exam LP instance")
    lp_path = _resolve_path(lp_value, base_dir=base_dir)
    if not lp_path.exists():
        raise FileNotFoundError(f"Exam LP file not found: {lp_path}")
    return lp_path


def _resolve_path(path_value: str | Path, *, base_dir: str | Path | None) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path.resolve()

    candidates: list[Path] = []
    if base_dir is not None:
        candidates.append((Path(base_dir).expanduser().resolve() / path).resolve())
    candidates.append((Path.cwd() / path).resolve())
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append((repo_root / path).resolve())

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _resolve_optional_path(path_value: str | Path | None, *, base_dir: str | Path | None) -> Path | None:
    if path_value in {None, ""}:
        return None
    return _resolve_path(path_value, base_dir=base_dir)


def _extract_weights_from_lp(lp_path: Path) -> Dict[str, float]:
    objective_lines: list[str] = []
    in_objective = False
    with lp_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            if line in {"Minimize", "Maximize"}:
                in_objective = True
                continue
            if not in_objective:
                continue
            if line.startswith("Subject To"):
                break
            objective_lines.append(line)

    objective_text = " ".join(objective_lines)
    weights: Dict[str, float] = {}
    for weight_name, var_name in _WEIGHT_TO_VAR.items():
        weights[weight_name] = _find_weight(objective_text, var_name)

    defaults = _default_weights()
    defaults.update(weights)
    return defaults


def _default_weights() -> Dict[str, float]:
    return {
        "alpha": 10.0,
        "beta": 10.0,
        "gamma1": 1.0,
        "gamma2": 1.0,
        "delta": 5.0,
        "vega": 1.0,
        "theta": 2.0,
        "lambda_large1": 1.0,
        "lambda_large2": 1.0,
        "lambda_big": 1000.0,
    }


def _find_weight(objective_text: str, var_name: str) -> float:
    explicit = re.search(
        rf"(?<![\w.])([+-]?\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+{re.escape(var_name)}\b",
        objective_text,
    )
    if explicit is not None:
        return float(explicit.group(1).replace(" ", ""))

    implicit = re.search(rf"([+-])\s*{re.escape(var_name)}\b", objective_text)
    if implicit is not None:
        return 1.0 if implicit.group(1) == "+" else -1.0

    bare = re.search(rf"\b{re.escape(var_name)}\b", objective_text)
    if bare is not None:
        return 1.0

    raise ValueError(f"Could not extract objective coefficient for {var_name} from LP objective")


def _compute_penalty_starts(
    blocks: list[int],
    slots_per_day: int,
) -> tuple[list[int], list[int], list[int], list[int]]:
    slots = list(blocks)
    slots_n = range(1, len(slots) + 1)
    d = dict(zip(slots, slots_n))
    slots_e = list(slots) + [np.inf] * 10

    triple_24_start: list[int] = []
    triple_day_start: list[int] = []
    eve_morn_start: list[int] = []
    other_b2b_start: list[int] = []

    for j in range(len(slots)):
        s = slots[j]
        if s + 1 == slots_e[j + 1]:
            if s % slots_per_day == 0:
                eve_morn_start.append(d[s])
            else:
                other_b2b_start.append(d[s])
            if s + 2 == slots_e[j + 2]:
                if (
                    slots_per_day - s % slots_per_day >= 2
                    and slots_per_day - s % slots_per_day != slots_per_day
                ):
                    triple_day_start.append(d[s])
                else:
                    triple_24_start.append(d[s])
    return triple_24_start, triple_day_start, eve_morn_start, other_b2b_start
