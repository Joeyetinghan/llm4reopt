"""Structured optimization model primitives."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, MutableMapping


class VariableType(str, Enum):
    """Supported variable domain types."""

    BINARY = "binary"
    INTEGER = "integer"
    CONTINUOUS = "continuous"


@dataclass
class VariableFamily:
    """Container for a homogeneous family of decision variables."""

    name: str
    index_set: List[Any]
    var_type: VariableType
    lower_bounds: MutableMapping[Any, float]
    upper_bounds: MutableMapping[Any, float]
    desc: str = ""
    tags: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "VariableFamily":
        return VariableFamily(
            name=self.name,
            index_set=list(self.index_set),
            var_type=self.var_type,
            lower_bounds=dict(self.lower_bounds),
            upper_bounds=dict(self.upper_bounds),
            desc=self.desc,
            tags=set(self.tags),
            aliases=set(self.aliases),
            metadata=_clone_mapping(self.metadata),
        )


@dataclass
class ConstraintFamily:
    """Symbolic description of a constraint family."""

    name: str
    index_set: List[Any]
    lhs_spec: Any
    rhs_spec: Any
    sense: str = "<="
    desc: str = ""
    tags: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "ConstraintFamily":
        return ConstraintFamily(
            name=self.name,
            index_set=list(self.index_set),
            lhs_spec=self.lhs_spec.copy() if hasattr(self.lhs_spec, "copy") else self.lhs_spec,
            rhs_spec=self.rhs_spec.copy() if hasattr(self.rhs_spec, "copy") else self.rhs_spec,
            sense=self.sense,
            desc=self.desc,
            tags=set(self.tags),
            aliases=set(self.aliases),
            metadata=_clone_mapping(self.metadata),
        )


@dataclass
class ObjectiveComponent:
    """Weighted objective contribution."""

    name: str
    weight: float
    spec: Any
    desc: str = ""
    tags: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "ObjectiveComponent":
        return ObjectiveComponent(
            name=self.name,
            weight=self.weight,
            spec=self.spec.copy() if hasattr(self.spec, "copy") else self.spec,
            desc=self.desc,
            tags=set(self.tags),
            aliases=set(self.aliases),
            metadata=_clone_mapping(self.metadata),
        )


@dataclass
class ParameterInfo:
    """Descriptive metadata for a structured parameter."""

    name: str
    desc: str = ""
    tags: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "ParameterInfo":
        return ParameterInfo(
            name=self.name,
            desc=self.desc,
            tags=set(self.tags),
            aliases=set(self.aliases),
            metadata=_clone_mapping(self.metadata),
        )


@dataclass
class StructuredModel:
    """Aggregate structured model with metadata and parameters."""

    variables: Dict[str, VariableFamily] = field(default_factory=dict)
    constraints: Dict[str, ConstraintFamily] = field(default_factory=dict)
    objectives: Dict[str, ObjectiveComponent] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    parameter_info: Dict[str, ParameterInfo] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    supports: Dict[str, Any] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "StructuredModel":
        return StructuredModel(
            variables={name: fam.copy() for name, fam in self.variables.items()},
            constraints={name: fam.copy() for name, fam in self.constraints.items()},
            objectives={name: obj.copy() for name, obj in self.objectives.items()},
            parameters=dict(self.parameters),
            parameter_info={name: info.copy() for name, info in self.parameter_info.items()},
            artifacts=_clone_mapping(self.artifacts),
            supports=_clone_mapping(self.supports),
            extras=_clone_mapping(self.extras),
        )

    def update_parameter(self, name: str, value: Any) -> None:
        self.parameters[name] = value

    def describe(self) -> dict[str, Any]:
        """Return lightweight summary helpful for debugging."""

        return {
            "n_variables": len(self.variables),
            "n_constraints": len(self.constraints),
            "n_objectives": len(self.objectives),
            "parameters": list(self.parameters.keys()),
            "parameter_info": list(self.parameter_info.keys()),
            "artifacts": sorted(str(key) for key in self.artifacts.keys()),
            "extras": sorted(str(key) for key in self.extras.keys()),
        }


def register_parameter(
    model: StructuredModel,
    name: str,
    value: Any,
    *,
    desc: str = "",
    tags: Iterable[str] = (),
    aliases: Iterable[str] = (),
    metadata: Dict[str, Any] | None = None,
) -> None:
    model.parameters[name] = value
    model.parameter_info[name] = ParameterInfo(
        name=name,
        desc=desc,
        tags=set(tags),
        aliases=set(aliases),
        metadata=dict(metadata or {}),
    )


def register_var_family(
    model: StructuredModel,
    family: VariableFamily,
) -> VariableFamily:
    model.variables[family.name] = family
    return family


def register_constraint_family(
    model: StructuredModel,
    family: ConstraintFamily,
) -> ConstraintFamily:
    model.constraints[family.name] = family
    return family


def register_objective_component(
    model: StructuredModel,
    objective: ObjectiveComponent,
) -> ObjectiveComponent:
    model.objectives[objective.name] = objective
    return objective


def _clone_mapping(mapping: Dict[str, Any]) -> Dict[str, Any]:
    cloned: Dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            cloned[key] = _clone_mapping(value)
        elif isinstance(value, list):
            cloned[key] = list(value)
        elif isinstance(value, set):
            cloned[key] = set(value)
        elif hasattr(value, "copy") and callable(value.copy):
            cloned[key] = value.copy()
        else:
            cloned[key] = value
    return cloned
