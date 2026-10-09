"""Template adapter scaffold for new problem packages."""

from __future__ import annotations

from typing import Any

from framework.core import Patch, PatchOp, ProblemSpec
from problems.base import BasePackagedProblemAdapter


class TemplateProblemAdapter(BasePackagedProblemAdapter):
    def normalize_patches(self, spec: ProblemSpec, event, patches: list[Patch], model=None) -> list[Patch]:
        del spec, event, model
        return list(patches)

    def supported_patch_ops(self) -> list[PatchOp]:
        return []

    def extract_warm_start(self, reopt_result) -> Any | None:
        del reopt_result
        return None

    def _build_env(self, spec: ProblemSpec):
        del spec
        raise NotImplementedError("Template problem does not implement execution")

    def _build_validator(self, spec: ProblemSpec):
        del spec
        raise NotImplementedError("Template problem does not implement execution")
