#!/usr/bin/env python3
"""CLI entry point for running packaged re-optimization problems."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framework.core import DeltaRequest
from framework.bundles import load_bundle
from framework.core.planner_modes import normalize_planner_mode
from framework.evaluation import print_run_report
from framework.execution import run_problem
from framework.llm import DEFAULT_LLM_MODEL
from framework.execution.strategy import canonical_strategy_policy_mode, default_requested_strategy_for_policy
from framework.prompting import PromptCatalogUnavailable, resolve_problem_prompt
from framework.registry import builtin_problem_roots, load_problem


def parse_args() -> argparse.Namespace:
    builtins = sorted(builtin_problem_roots())

    parser = argparse.ArgumentParser(description="Run packaged ReOpt problems")
    parser.add_argument(
        "--problem",
        default=None,
        choices=builtins,
        help="Built-in packaged problem to run",
    )
    parser.add_argument(
        "--problem-root",
        default=None,
        help="Path to a packaged problem root containing problem.yaml",
    )
    parser.add_argument(
        "--bundle",
        default=None,
        help="Path to a runtime bundle directory or zip containing context.md and model.py/model.lp",
    )
    parser.add_argument(
        "--task",
        default="transport",
        choices=builtins,
        help="Legacy compatibility alias for --problem",
    )
    parser.add_argument("--config", default=None, help="Problem config path override")
    parser.add_argument(
        "--delta",
        action="append",
        default=None,
        help="Override delta text; pass multiple times for sequential re-optimization",
    )
    parser.add_argument(
        "--experience",
        default=None,
        help="Optional examples/history JSON path; legacy alias now mapped into packaged context",
    )
    parser.add_argument(
        "--model",
        "--gemini-model",
        dest="model",
        default=DEFAULT_LLM_MODEL,
        help="LLM model name (e.g. gpt-4.1, o3, gemini-2.5-flash-lite)",
    )
    parser.add_argument(
        "--api-key",
        "--gemini-api-key",
        dest="api_key",
        default=None,
        help="Optional API key override",
    )
    parser.add_argument(
        "--planner-mode",
        default=None,
        help=(
            "Planner backend mode: patchedit, patchedit-two-stage, or codeedit "
            "(deprecated aliases like integrated and split are still accepted)."
        ),
    )
    parser.add_argument(
        "--strategy-policy",
        choices=["rule", "llm", "deterministic"],
        default=None,
        help="Solve-strategy selector policy; defaults to llm for the main CLI",
    )
    parser.add_argument(
        "--strategy",
        choices=["auto", "scratch", "warm", "tuned", "warm+tuned"],
        default=None,
        help="Optional fixed solve strategy override; deterministic mode defaults to scratch",
    )
    parser.add_argument(
        "--strategy-selector-model",
        default=None,
        help="Optional model override for the strategy selector",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.bundle and args.problem_root:
        raise SystemExit("--bundle cannot be combined with --problem-root")
    if args.bundle and args.problem:
        raise SystemExit("--bundle cannot be combined with --problem")
    if args.bundle and args.config:
        raise SystemExit("--bundle does not accept --config")
    if args.bundle and not args.delta:
        raise SystemExit("--bundle requires at least one --delta")

    if args.bundle:
        spec, adapter = load_bundle(
            bundle_path=args.bundle,
            examples_path=args.experience,
        )
    else:
        problem_name = args.problem or args.task
        spec, adapter = load_problem(
            problem=problem_name if args.problem_root is None else None,
            problem_root=args.problem_root,
            config_path=args.config,
            examples_path=args.experience,
        )
    selected_policy = canonical_strategy_policy_mode(args.strategy_policy, default="llm")
    requested_strategy = args.strategy or default_requested_strategy_for_policy(
        selected_policy,
        default_policy="llm",
    )
    if args.planner_mode:
        spec.config_metadata["planner_mode"] = normalize_planner_mode(args.planner_mode)
    spec.config_metadata["strategy_policy"] = selected_policy
    if args.strategy:
        spec.config_metadata["strategy"] = args.strategy
    if args.strategy_selector_model:
        spec.config_metadata["strategy_selector_model"] = args.strategy_selector_model
    if args.delta:
        delta_requests = [DeltaRequest(text=delta_text) for delta_text in args.delta]
    else:
        default_request = _default_delta_request(spec)
        delta_requests = [default_request]

    result = run_problem(
        adapter,
        spec,
        delta_requests,
        model_name=args.model,
        api_key=args.api_key,
        requested_strategy=requested_strategy,
    )
    print_run_report(result)


def _default_delta_request(spec) -> DeltaRequest:
    loaded = spec.config_metadata.get("loaded_data") or {}
    config = spec.config_metadata.get("config") or {}
    delta_text = loaded.get("delta_text") or config.get("delta_text")
    if delta_text:
        return DeltaRequest(text=str(delta_text))

    prompt_id = loaded.get("prompt_id") or config.get("prompt_id")
    prompt_params = loaded.get("prompt_params") or config.get("prompt_params")
    if prompt_id:
        try:
            prompt = resolve_problem_prompt(
                spec.metadata.problem_id,
                str(prompt_id),
                params=prompt_params if isinstance(prompt_params, dict) else None,
            )
            return DeltaRequest(
                text=prompt.text,
                metadata={
                    "prompt_id": str(prompt_id),
                    **dict(prompt.metadata),
                },
            )
        except PromptCatalogUnavailable:
            pass

    return DeltaRequest(text="Describe the requested change.")


if __name__ == "__main__":
    main()
