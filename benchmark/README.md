# Exam Re-Optimization Benchmark

This is a solver-free benchmark for **LLM-guided re-optimization**. You start from a deployed final-exam schedule and a natural-language change request. The task is to produce a new schedule that satisfies the request and scores well under the model's objective.

It accompanies *Democratizing Large-Scale Re-Optimization with LLM-Guided Model Patches* ([arXiv:2605.18692](https://arxiv.org/abs/2605.18692)) and packages the paper's Cornell exam case study:

- **5 instances** (`instances/I1.json` … `I5.json`) with their deployed (base) schedules, and the generator's raw files behind them (`raw_instances/`);
- **30 tasks** (`tasks.jsonl`), one per instance × change request P1–P6. Each task has a gold structured edit and a 1-hour Gurobi reference schedule;
- **`reopt_exam.py`**, an evaluator built on the standard library only. It applies a gold edit, checks feasibility, and reproduces the MIP objective and the paper's schedule-quality metrics.

This folder holds the inputs only. What the paper's experiments produced is in [`outputs/`](../outputs/): the 270 recorded LLM runs with their trajectories and the paper's tables in [`outputs/llm_runs/`](../outputs/llm_runs/README.md), and the base and reference Gurobi solves in [`outputs/solves/`](../outputs/solves/README.md).

Command-line examples are in the [quick start](../README.md#quick-start-the-benchmark-no-solver-needed). `--schedule` takes a JSON file (a `{"block": slot}` mapping, or any object with a `schedule` field, such as a record of `outputs/llm_runs/runs.jsonl`), `base` for the deployed schedule, or `reference` for the task's reference schedule. The exit code is 0 if the schedule is feasible under the gold edit, 1 if it is not, and 2 if the input is unusable (missing file, malformed JSON, duplicate keys, non-integer ids, unknown task or instance).

### Files at a glance

| file | rows | content | fields |
| --- | --- | --- | --- |
| `instances/I1.json` … `I5.json` | 5 | block sets, enrollments, pair/triplet co-enrollment counts, weights, base schedule | [FIELDS.md](FIELDS.md#instancesijson-5-files) |
| `raw_instances/<name>/` | 5 dirs | the generator's CSV and JSON files, from which the MIP is built | [raw_instances/README.md](raw_instances/README.md) |
| `tasks.jsonl` | 30 | prompt, gold edit, assumptions, reference schedule | [FIELDS.md](FIELDS.md#tasksjsonl-30-lines) |
| `MANIFEST.json` | | version, generator commit, checksums | [FIELDS.md](FIELDS.md#manifestjson), [Integrity](#integrity) |
| `DATASHEET.md` | | provenance, collection, privacy, known limitations, intended uses | |
| `reopt_exam.py` | | evaluator (standard library only) | `python benchmark/reopt_exam.py --help` |

### Loading the data

Everything is plain JSON / JSONL / CSV:

```python
import json, sys
sys.path.insert(0, "benchmark")
import reopt_exam

inst = reopt_exam.load_instance("I2")      # dict; pair/triplet counts become {(i, j): count} lookups
tasks = reopt_exam.load_tasks()            # {"I2_P5": {...}, ...}

task = tasks["I2_P5"]
print(task["prompt"])                    # natural-language change request
print(task["gold_edit"])                 # its structured form
result = reopt_exam.evaluate_task(task, task["reference"]["schedule"])
print(result["feasible"], result["objective"], result["gap_to_reference_pct"])
```

With pandas, `pd.read_json("benchmark/tasks.jsonl", lines=True)`. To read an instance file without the helper, use `json.load`; there, `pair_counts` and `triplet_counts` are lists of `[i, j, count]` and `[i, j, k, count]`.

## Problem

Exams are grouped into *blocks*, and the task is to sequence the blocks into exam *slots*. Slots are numbered from 1, there are 3 slots per day (9am, 2pm, 7pm), so slot `s` is on day `(s − 1) // 3 + 1`. The number of slots equals the number of blocks, and each slot holds exactly one block. *Virtual* blocks are empty placeholders with no enrollment and no co-enrollment, so they act as free slots and are interchangeable. I3 has 25 slots, so its ninth day has only a morning slot. A schedule is a JSON object mapping every block id (real and virtual) to a distinct slot id; ids must be integers (JSON keys may be digit strings):

```json
{"1": 3, "2": 16, "3": 11, "...": "..."}
```

The objective (minimized) penalizes co-enrolled students, using the ordered counts `pair_counts[(i, j)]` and `triplet_counts[(i, j, k)]` of the blocks in consecutive slots:

| weight | event | counted for windows starting at |
| --- | --- | --- |
| `gamma1` | evening → next-morning back-to-back (pair) | `penalty_starts.eve_morn_start` |
| `gamma2` | other back-to-backs (pair) | `penalty_starts.other_b2b_start` |
| `alpha` | three exams in one day (triple) | `penalty_starts.triple_day_start` |
| `beta` | three exams in 24 hours spanning two days (triple) | `penalty_starts.triple_24_start` |
| `delta` | three exams within four consecutive slots `i, j, k, l`: `t(i,j,k) + t(i,k,l)` | slots `s` where `s` and `s + 1` are both triple starts |

No penalty window runs past the last slot in any instance, so the schedule is effectively linear (the evaluator indexes slots modulo the slot count, as the MIP does, but the start sets never use it). Base weights: `gamma1 = gamma2 = 1`, `alpha = beta = 10`, `delta = 5`.

Hard constraints: every block is placed in exactly one slot and every slot holds one block; every *large* block is placed in an `early_slots` slot (base: slots 1–21). A real block is **large** if it contains at least one exam with more than 300 students (`metadata.frontload_block_size_cutoff`); this is the generator's rule (`compute_large_blocks` at commit `9a834d1`), not a threshold on total block enrollment. Exam-level sizes are not shipped, so `large_blocks` is given explicitly.

## Instances

| id | enrollment profile | exams | real blocks | slots | large blocks | base objective | base bound | base gap |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| I1 | Spring 2024 | 544 | 20 | 24 | 19 | 5338 | 4015 | 24.8% |
| I2 | Fall 2023 | 601 | 18 | 24 | 18 | 7330 | 4631 | 36.8% |
| I3 | Spring 2023 | 553 | 17 | 25 | 16 | 4218 | 3286 | 22.1% |
| I4 | Fall 2022 | 588 | 19 | 24 | 18 | 6009 | 4196 | 30.2% |
| I5 | Spring 2022 | 539 | 16 | 24 | 14 | 2577 | 2048 | 20.5% |

The instances are synthetic and Cornell-like; their provenance, the generator and the paper to cite with them are in [`DATASHEET.md`](DATASHEET.md#collection-process). Each base solution is the deployed schedule, the incumbent of a Gurobi solve of the original model (`instances/*.json → base_solution`; solve settings, full solution files and logs in [`outputs/solves/`](../outputs/solves/README.md)). The full MIP (`model.lp`) can be rebuilt from the files in `raw_instances/` ([`prepare_data`](../README.md#running-reopt-llm)). Every instance field is defined in [`FIELDS.md`](FIELDS.md#instancesijson-5-files).

## Tasks

| prompt | change request (abridged; exact text in `tasks.jsonl`) | gold edit `op` |
| --- | --- | --- |
| P1 | Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events. | `reserve_slot_for_virtual_block` |
| P2 | Increase the pairwise co-enrollment count between Block 4 and Block 9 by 120 students due to late add/drop changes. | `increase_pair_count` |
| P3 | Ensure all large exams with over 300 students are completed before the 20th time slot to allow teaching assistants sufficient grading time. | `set_early_slots` |
| P4 | Increase the penalty for having "three exams in 24 hours" to be 20 times that of a regular back-to-back. | `set_weights` |
| P5 | Limit the total number of students taking exams on Day 2 to a maximum of 4,000. | `slot_load_cap` |
| P6 | Apply P4, then P2, then P1. | `sequence` |

Only the P3 cutoff varies by instance (`prompt_params`). Each line of `tasks.jsonl` holds the exact prompt, its parameters, the gold edit, the interpretation assumptions and the reference solve, a 1-hour Gurobi solve of the gold-edited model ([fields](FIELDS.md#tasksjsonl-30-lines)).

Gold edit operators (`reopt_exam.apply_edit`):

| op | fields | meaning |
| --- | --- | --- |
| `reserve_slot_for_virtual_block` | `slot` (optional `block`) | `slot` must hold a virtual block, i.e. stay empty ([P1 note](#tasks)). An integer `block` pins one specific virtual block (not used by the gold edits). |
| `increase_pair_count` | `pairs`, `delta` | add `delta` to each ordered pair count |
| `set_early_slots` | `early_slots` | large blocks must be placed in these slots |
| `set_weights` | `weights` | override objective weights |
| `slot_load_cap` | `slots`, `cap`, `blocks` | sum of `block_enrollment` over real blocks placed in `slots` ≤ `cap` |
| `sequence` | `edits` | apply edits in order |

Interpretation notes:

- **P1**: the reference solve fixed the smallest virtual block id in the reserved slot; since virtual blocks are interchangeable, the gold edit accepts any of them.
- **P2, P4** change only the objective. They keep the base hard constraints unchanged, so any schedule feasible for the base problem (including the base schedule) stays feasible. Feasibility alone therefore cannot show whether the edit was right; correctness shows up only in the objective (`objective` and `gap_to_reference_pct` of `evaluate_task`).
- **P4** is read as `gamma1 = gamma2` (an evening–morning back-to-back is a regular back-to-back) and `beta = 20 · gamma2`.
- **P5** caps exam seats, not distinct students: `block_enrollment` sums exam enrollments, so a student with two day-2 exams counts twice. The aggregated data cannot count distinct students.

### Scoring

`reopt_exam.evaluate_task(task, schedule)` returns

- `feasible` and `violations`: hard constraints checked under the gold-edited model;
- `objective`: the gold-edited MIP objective (`null` if the schedule does not assign every block to a distinct slot);
- `gap_to_reference_pct = 100 · (objective − reference) / reference`, where *reference* is the task's reference objective. Negative values beat the reference. The gap is only meaningful for feasible schedules;
- `metrics`: the student-facing counts reported in the paper, computed on the **unedited** counts so they are comparable across prompts.

The references are 1-hour incumbents, not proven optima: their MIP gaps range from 17.6% to 55.5% (median 26.6%), and 30 of the paper's gold-feasible runs beat their reference. Report the reference's `obj_bound` alongside gaps if you need an optimality certificate.

The paper metrics count windows that end at or before the last slot, use unordered co-enrollment lookups, and differ from the objective terms:

| metric | definition |
| --- | --- |
| `back_to_back_count` | pair count of every two adjacent slots (all pair penalty starts) |
| `triple_count` | triplet count of every three-slot window at a triple penalty start (same day or 24 h) |
| `two_in_24hr_count` | pair count of every two slots at distance 1–3 |
| `three_in_4_slots_count` | for every four consecutive slots, the sum over **all four** 3-of-4 subsets (the objective's `delta` term uses two) |

Gurobi reports objectives like `5408.99995`, so compare objectives with a relative tolerance; all 30 reference schedules reproduce their reported objective within `1e-6` under the gold edit.

## Using the benchmark for re-optimization research

Each task is a pair (base problem, modified problem) with a known solved base state, so it fits *warm-started* or *verified* re-optimization: reuse the base schedule, its bound, or solver state, and certify the result on the modified problem. The prompt, the gold edit, and the 270 recorded LLM trajectories ([`outputs/llm_runs/`](../outputs/llm_runs/README.md)) make it possible to study the whole pipeline (understand the request → edit the model → choose a reuse strategy → certify) rather than the solver step alone.

| task | change class | encoding in the MIP and in the MPS export |
| --- | --- | --- |
| P1 | bounds | real blocks fixed out of the reserved slot |
| P2, P4 | objective | new pair-count / weight coefficients |
| P3 | bounds | the large blocks' allowed slots shrink: frontload rows in the MIP, bound fixings in the MPS export (exactly equivalent, also for the LP relaxation) |
| P5 | rhs | one new knapsack-type row over the three day-2 slots; in the MPS export the row is present in the base with a non-binding right-hand side and the task sets the cap |
| P6 | objective + bounds | composition of P4, P2, P1 |

### MPS model export (optional, needs gurobipy)

`python -m scripts.export_mps [--only I5] [--tasks I5_P1 ...] [--out mps/]` writes each instance as a re-optimization series: `mps/<I>/base.mps.gz` plus, per task, `<task>/task.mps.gz`. All models of an instance share identical variable and constraint names and order, so every task is a pure data change of the base model, as in the table above.

`base.sol.gz` (the deployed schedule) and `reference.sol.gz` are full variable assignments that Gurobi reads directly; `change.json` holds the prompt, gold edit, change classes and diff counts, and `diff.csv.gz` every changed coefficient. Models are built from `instances/<I>.json`, so `scripts/prepare_data.py` is not needed. The script re-reads its output and checks that each reference solution is feasible and scores exactly its `reopt_exam.evaluate_task` objective. Models have about 0.68M binary variables and 0.40M rows (I3: 0.80M and 0.47M), so a license beyond the size-limited pip one is required. Expect about 70 s and 130 MB per instance (about 700 MB in total); `mps/` is git-ignored.

## Ideas for use

- **Text-to-edit**: predict `gold_edit` from `prompt` plus the instance, and compare it with the gold edit (or solve the predicted model and score its schedule with `evaluate_task`).
- **Re-optimization heuristics**: start from `base_solution` and compete with the 1-hour reference without a MIP solver.
- **Warm-start and reuse policies**: decide which parts of the base solution or bound to reuse for each change class, and measure time to a target gap.
- **Agent analysis**: study failure modes, repair loops, and the gap between code agents and structured patches using the recorded runs and trajectories in [`outputs/llm_runs/`](../outputs/llm_runs/README.md).
- **New requests**: compose new structured edits with `reopt_exam.apply_edit` and generate new tasks.

## Integrity

`MANIFEST.json` records the dataset version, the generator commit, and SHA-256 checksums of every other file in `benchmark/` (paths relative to the repository root). To check them from the repository root:

```bash
python -c "import hashlib, json; m = json.load(open('benchmark/MANIFEST.json')); bad = [p for p, h in m['files'].items() if hashlib.sha256(open(p, 'rb').read()).hexdigest() != h]; print(bad or 'ok')"
```
