# Framework Overview

The generic runtime is split into a small number of modules:

- `core/`: typed schemas, adapter interfaces, strategy interface, and compatibility exports for structured model and patch primitives
- `prompting/`: prompt-context assembly plus examples/history loading
- `editing/`: the code-edit workspace and aider driver, and the validator that solves edited models
- `execution/`: registry-driven run pipeline plus LLM and deterministic solve strategy selection
- `evaluation/`: standardized evaluation summaries and CLI reporting
- `registry/`: packaged problem discovery and manifest loading

## Primary Types

- `ProblemSpec`: packaged problem metadata, model artifacts, data bundle, context bundle, config metadata, capabilities
- `DeltaRequest`: portable natural-language change request
- `PromptContext`: LLM-facing context assembled from the package and current structured model
- `ReoptResult`: one delta-step result
- `ProblemRunResult`: full run result including base solve and one or more steps

## Adapter Role

Each packaged problem implements a `ProblemAdapter` that:

- loads the package manifest and config
- builds the structured model
- assembles prompt context
- normalizes candidate patches
- builds the env and validator that solve the base and edited models
- extracts warm starts and evaluates solve output

Methods either apply structured patches (patch-edit) or edit the model code directly (code-edit). User-facing CLI and batch runs default to `LLMBasedSolveStrategyPolicy`. `RuleBasedSolveStrategyPolicy` remains available for deterministic baselines and ablations.
