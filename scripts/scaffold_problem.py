#!/usr/bin/env python3
"""Scaffold a new packaged problem from the template package."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROBLEMS_ROOT = REPO_ROOT / "problems"
TEMPLATE_ROOT = PROBLEMS_ROOT / "template_problem"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a new packaged problem scaffold")
    parser.add_argument("--problem-id", required=True, help="Package directory and problem id, e.g. facility_flow")
    parser.add_argument("--name", required=True, help="Human-readable problem name")
    parser.add_argument(
        "--description",
        default=None,
        help="Optional manifest description. Defaults to '<name> packaged behind the generic interface.'",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROBLEMS_ROOT),
        help="Directory where the new problem package should be created",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package_root = scaffold_problem(
        problem_id=args.problem_id,
        name=args.name,
        description=args.description,
        output_root=Path(args.output_root),
    )
    print(f"Created scaffold: {package_root}")


def scaffold_problem(
    *,
    problem_id: str,
    name: str,
    description: str | None = None,
    output_root: Path,
) -> Path:
    normalized_problem_id = _normalize_problem_id(problem_id)
    package_root = output_root.expanduser().resolve() / normalized_problem_id
    if package_root.exists():
        raise FileExistsError(f"Target package already exists: {package_root}")

    class_name = f"{_snake_to_camel(normalized_problem_id)}ProblemAdapter"
    description_text = description or f"{name} packaged behind the generic interface."

    shutil.copytree(
        TEMPLATE_ROOT,
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )

    _rewrite_file(
        package_root / "problem.yaml",
        {
            "problems.template_problem.adapter:TemplateProblemAdapter": f"adapter:{class_name}",
            "template_problem": normalized_problem_id,
            "Template Problem": name,
            "Scaffold showing how to package a new optimization problem for the generic framework.": description_text,
            "TemplateProblemAdapter": class_name,
        },
    )
    _rewrite_file(
        package_root / "adapter.py",
        {
            "Template adapter scaffold for new problem packages.": f"{name} adapter scaffold.",
            "TemplateProblemAdapter": class_name,
            "Template problem does not implement execution": f"{name} does not implement execution",
        },
    )
    _rewrite_file(
        package_root / "__init__.py",
        {
            '"""Template packaged problem."""': f'"""{name} packaged problem."""',
            "TemplateProblemAdapter": class_name,
        },
    )
    _rewrite_file(
        package_root / "env.py",
        {
            '"""Template model entrypoint scaffold for new packaged problems."""': (
                f'"""{name} model entrypoint scaffold."""'
            ),
            "Template problem does not define a model builder": f"{name} does not define a model builder",
        },
    )
    _rewrite_file(
        package_root / "context" / "problem_context.md",
        {"# Template Problem Context": f"# {name} Problem Context"},
    )

    return package_root


def _rewrite_file(path: Path, replacements: dict[str, str]) -> None:
    content = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        content = content.replace(old, new)
    path.write_text(content, encoding="utf-8")


def _normalize_problem_id(problem_id: str) -> str:
    candidate = problem_id.strip().lower().replace("-", "_").replace(" ", "_")
    if not candidate:
        raise ValueError("Problem id must not be empty")
    if any(ch for ch in candidate if not (ch.isalnum() or ch == "_")):
        raise ValueError(f"Problem id must contain only letters, digits, or underscores: {problem_id!r}")
    return candidate


def _snake_to_camel(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_") if part)


if __name__ == "__main__":
    main()
