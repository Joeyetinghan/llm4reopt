# Field Reference

Every field of the instance, task and manifest files in `benchmark/`. The recorded runs of the paper are documented in [`outputs/llm_runs/FIELDS.md`](../outputs/llm_runs/FIELDS.md). For what the benchmark is and how it is scored, see [`README.md`](README.md); for provenance and limitations, see [`DATASHEET.md`](DATASHEET.md).

Conventions: block and slot ids are integers starting at 1 (JSON object keys are digit strings). `null` means "not applicable" or "not available". Objectives are minimized.

## `instances/<I>.json` (5 files)

| field | type | meaning |
| --- | --- | --- |
| `instance_id` | str | `I1` … `I5` |
| `name` | str | name of the raw instance directory in `raw_instances/` ([naming](raw_instances/README.md#instance-names)) |
| `description` | str | one-line description with the enrollment profile |
| `slots` | list[int] | slot ids `1 … n`, where `n` equals the number of blocks (real + virtual) |
| `slots_per_day` | int | 3 |
| `slot_times` | list[str] | `["9am", "2pm", "7pm"]`: the time of slot `s` is `slot_times[(s − 1) % 3]` |
| `blocks` | list[int] | all block ids, `1 … n`: real blocks first, virtual blocks last |
| `real_blocks` | list[int] | blocks that contain exams |
| `virtual_blocks` | list[int] | empty placeholder blocks (no exams, no enrollment, no co-enrollment); interchangeable |
| `large_blocks` | list[int] | real blocks containing an exam with more than 300 students; must go in `early_slots` |
| `early_slots` | list[int] | slots allowed for large blocks (base: 1–21) |
| `penalty_starts` | object | the slots at which each penalty window starts: `eve_morn_start`, `other_b2b_start`, `triple_day_start`, `triple_24_start` ([objective table](README.md#problem)) |
| `weights` | object | objective weights `gamma1`, `gamma2`, `alpha`, `beta`, `delta` |
| `block_enrollment` | object | block id → total exam seats in the block (sum of its exams' enrollments) |
| `block_num_exams` | object | block id → number of exams in the block |
| `pair_counts` | list of `[i, j, count]` | students taking an exam in block `i` and one in block `j`. Symmetric: both `[i, j, c]` and `[j, i, c]` are listed. Diagonal entries `[i, i, c]` count students with two exams in the same block; they never enter the objective. Missing pairs are 0. |
| `triplet_counts` | list of `[i, j, k, count]` | students with exams in blocks `i`, `j` and `k`, for ordered triples (repeated ids possible, e.g. `[1, 1, 2, c]`). Missing triples are 0. |
| `metadata` | object | `reference_term` (enrollment profile), `num_exams`, `num_blocks`, `num_real_blocks`, `num_slots`, `seed` (42), `frontload_block_size_cutoff` (300 students per exam), `frontload_slot_cutoff` (last early slot, 21), `generator` (URL of the instance generator), `source_dir` (raw files, `benchmark/raw_instances/<name>`) |
| `base_solution` | object | the deployed schedule (below) |

`base_solution`:

| field | meaning |
| --- | --- |
| `schedule` | block id → slot id |
| `objective`, `obj_bound`, `mip_gap` | incumbent objective, best bound and relative gap of the 24-hour solve |
| `runtime_sec`, `status`, `solver` | solve time (about 86,400 s), Gurobi status (`TIME_LIMIT`), solver version (`Gurobi 13.0.1`) |
| `source` | the full Gurobi solution file, `outputs/solves/base/<name>.sol.gz` |
| `metrics` | `triple_count`, `back_to_back_count`, `two_in_24hr_count`, `three_in_4_slots_count` of the schedule ([definitions](README.md#scoring)) |

## `tasks.jsonl` (30 lines)

| field | type | meaning |
| --- | --- | --- |
| `task_id` | str | `<instance>_<prompt>`, e.g. `I2_P5` |
| `instance_id`, `prompt_id` | str | `I1` … `I5`; `P1` … `P6` |
| `prompt` | str | the exact natural-language change request given to the LLM |
| `prompt_params` | object | instance-specific values used to render the prompt; only P3 has them: `slot_cutoff_exclusive` (large blocks must finish before this slot), `slot_cutoff_ordinal` (its wording, e.g. `"20th"`), `early_slot_count` |
| `gold_edit` | object | the request as a structured edit: `op`, its fields ([operators](README.md#tasks)), and a human-readable `description`. `sequence` edits hold their sub-edits in `edits`. |
| `assumptions` | list[str] | interpretation choices for ambiguous wording |
| `reference` | object | the reference re-optimization (below) |

`reference`:

| field | meaning |
| --- | --- |
| `schedule` | block id → slot id, feasible under `gold_edit` |
| `objective`, `obj_bound`, `mip_gap`, `status` | 1-hour Gurobi solve of the gold-edited model (`TIME_LIMIT` for all 30) |
| `time_limit_sec`, `runtime_sec` | 3600 and the measured wall-clock time of the solve (`wall_time_s` in `outputs/solves/reference/`) |
| `execution_label` | how the solve was run: `direct+heuristic+tuned` = warm start from the base schedule (`direct`), plus a heuristic warm start, with tuned Gurobi parameters from `outputs/solves/tune/params/` |
| `metrics` | the four schedule-quality counts of `schedule` |

## `MANIFEST.json`

| field | meaning |
| --- | --- |
| `name`, `version`, `paper` | dataset name, semantic version, paper id |
| `generator` | `url`, `commit` and `seed` of the instance generator |
| `files` | path (relative to the repository root) → SHA-256, for every file in `benchmark/` except `MANIFEST.json` |
