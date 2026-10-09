# ReOpt-LLM: LLM-Guided Re-Optimization with Model Patches

Code, data, and LLM trajectories for the university exam scheduling case study in

> **Democratizing Large-Scale Re-Optimization with LLM-Guided Model Patches**
> Tinghan Ye, Arnaud Deza, Ved Mohan, El Mehdi Er Raqabi, Pascal Van Hentenryck
> arXiv:2605.18692, 2026. [[paper]](https://arxiv.org/abs/2605.18692)

ReOpt-LLM lets an end user change a deployed optimization model through natural language. An LLM turns the request into a structured patch of the model, or into a direct code edit for the baseline. It then picks a re-optimization technique (warm start, heuristic warm start, tuned solver parameters) and the edited model is solved.

## Repository layout

All data is in the repository as plain JSON, CSV, gzipped Gurobi solution files and logs (about 65 MB); there is nothing to download separately. `benchmark/` holds what a method runs on, and `outputs/` holds what the paper's Cornell case study produced. Reading and scoring them needs neither Gurobi nor an LLM API; running the framework needs both.

| location | content | documentation |
| --- | --- | --- |
| `benchmark/` | 5 instances with base schedules, the generator's raw files, 30 tasks with gold edits and reference schedules, the evaluator | [`README.md`](benchmark/README.md), [`FIELDS.md`](benchmark/FIELDS.md), [`DATASHEET.md`](benchmark/DATASHEET.md) (provenance and limitations) |
| `outputs/` | the paper's Gurobi solves (`solves/`) and its 270 LLM runs with trajectories, evaluation CSVs, tables and figure (`llm_runs/`) | [`README.md`](outputs/README.md) |
| `framework/` | shared runtime: schemas, patch DSL, planners, code editing, execution, evaluation | [`README.md`](framework/README.md) |
| `problems/` | problem packages: `exam_block_seq/` (model, patch surface, prompts, gold edits), `transport/` (toy), `template_problem/` | |
| `scripts/` | CLIs: `run_task`, `run_batch_experiments`, `prepare_data`, `export_mps`, exam campaign and evaluation tools | |

New runs, reference solves, tuning runs and re-evaluations go to the git-ignored `runs/`; by default, no shipped file in `outputs/` is overwritten (`prepare_data` only adds git-ignored files next to them).

## Quick start: the benchmark (no solver needed)

```bash
python benchmark/reopt_exam.py --task I1_P3 --schedule reference       # the 1-hour reference: feasible, gap 0
python benchmark/reopt_exam.py --task I1_P3 --schedule base            # the deployed schedule: violates P3 (exit code 1)
python benchmark/reopt_exam.py --task I1_P3 --schedule my_schedule.json  # your own {"block": slot, ...} JSON
```

To write a schedule file to start from (the base schedule, which you can then edit):

```bash
python -c "import json; json.dump(json.load(open('benchmark/instances/I1.json'))['base_solution']['schedule'], open('my_schedule.json', 'w'), indent=1)"
```

[`benchmark/README.md`](benchmark/README.md) covers the evaluator, the schedule format, the Python API, the tasks and the scoring.

## Running ReOpt-LLM

```bash
conda env create -f environment.yml
conda activate reopt-llm
python -m scripts.prepare_data      # rebuild benchmark/raw_instances/*/model.lp and unpack base solutions
```

`prepare_data` rebuilds each instance LP (about 0.7M binary variables) from the CSV files with `gurobipy` and writes it into the checkout (`benchmark/raw_instances/*/model.lp`, 80–95 MB each; `outputs/solves/base/*.sol`). These files are git-ignored. Use `--only <raw instance name>` (e.g. `--only blockseq_n544_blocks20_slots24_seed42` for I1; names in [`benchmark/raw_instances/README.md`](benchmark/raw_instances/README.md#instance-names)) to build one instance's LP, `--skip-lp` to only unpack the solutions. Building the LPs needs a Gurobi license that allows models of that size, such as a free academic license.

To use the tasks as MPS re-optimization series (base and task models with identical names, plus base and reference solutions), run `python -m scripts.export_mps`; see [`benchmark/README.md`](benchmark/README.md#mps-model-export-optional-needs-gurobipy).

LLM credentials are read from the environment or from a `.env` file in the repository root. A `.env` template (git-ignored):

```dotenv
# public OpenAI API
OPENAI_API_KEY=sk-your-key

# or Azure OpenAI: the key also goes in OPENAI_API_KEY
# OPENAI_AZURE_ENDPOINT=https://your-resource.openai.azure.com/
# OPENAI_AZURE_API_VERSION=2024-12-01-preview   # the default if unset
# OPENAI_AZURE_DEPLOYMENT_PREFIX=your-prefix-   # deployment name = prefix + model name

# optional, for Gemini models
# GEMINI_API_KEY=your-key
```

In a shell, use `export OPENAI_API_KEY=sk-your-key` instead.

Run a single change request:

```bash
# Structured patch planner with the LLM strategy selector (ReOpt-LLM-Patch)
python -m scripts.run_task --problem exam_block_seq \
  --config problems/exam_block_seq/configs/default.yaml \
  --planner-mode patchedit --model gpt-4.1 \
  --delta "Increase the pairwise co-enrollment count between Block 4 and Block 9 by 120 students."

# Direct code-edit baseline (Direct-Code Agent, uses aider)
python -m scripts.run_task --problem exam_block_seq \
  --config problems/exam_block_seq/configs/default.yaml \
  --planner-mode codeedit --model gpt-4.1 \
  --delta "Limit the total number of students taking exams on Day 2 to 4,000."
```

The toy transportation problem needs no data preparation and runs with a free size-limited Gurobi license (an LLM API key is still needed, since the change request is planned by the LLM):

```bash
python -m scripts.run_task --problem transport --delta "Set demand for customer C2 to 12"
```

To reproduce the paper's 270 runs, run the shipped run manifests, which hold each run's exact settings ([`outputs/llm_runs/README.md`](outputs/llm_runs/README.md#rerunning)):

```bash
python -m scripts.exam_block_seq.run_paper_campaign --dry-run             # list the 270 commands
python -m scripts.exam_block_seq.run_paper_campaign                       # run them all, one after another
python -m scripts.exam_block_seq.run_paper_campaign --only 'I1_*_prompts_P3'   # or a subset
```

It runs the 180 LLM runs first (`python -m scripts.run_batch_experiments <manifest>`), then the 90 runs without the selector (`scripts.exam_block_seq.replay_patchedit_plan`), which [replay the selector runs' patch plans](outputs/llm_runs/README.md#runs). Results go to the git-ignored `runs/exam/paper_full_gpt_3600s/`. A run can take more than an hour, since each solve may use the full 3600 s. Runs are independent, so to use a cluster, run different `--only` patterns as separate jobs, with the selector runs before the replays. LLM outputs are not deterministic, so a rerun gives new trajectories; score them with `benchmark/reopt_exam.py`. The paper's evaluator, and which of its columns it can rebuild for a new campaign, is described in [`outputs/llm_runs/README.md`](outputs/llm_runs/README.md#paper-evaluation-files).

The 1-hour reference solves come from `python -m scripts.exam_block_seq.benchmark_ground_truth --instance I1 --prompt P1 --time-limit 3600` (one case; omit `--instance`/`--prompt` for all 30), which applies each task's gold edit with `build_reference` in `problems/exam_block_seq/ground_truth/reference.py`. New reference solves go to `runs/ground_truth/`, and `python -m scripts.exam_block_seq.tune_default` writes new tuned parameters to `runs/tune/`; the paper's are in [`outputs/solves/`](outputs/solves/README.md).

To add a new problem, start from `python -m scripts.scaffold_problem --problem-id my_problem --name "My Problem"`, which copies `problems/template_problem/` into a new package.

## License

- Code: MIT ([`LICENSE`](LICENSE))
- Data, benchmark, runs, trajectories and results (`benchmark/` except `benchmark/reopt_exam.py`, and `outputs/`): CC BY 4.0 ([`LICENSE-DATA`](LICENSE-DATA))

## Citation

```bibtex
@article{ye2026democratizing,
  title   = {Democratizing Large-Scale Re-Optimization with {LLM}-Guided Model Patches},
  author  = {Ye, Tinghan and Deza, Arnaud and Mohan, Ved and Er Raqabi, El Mehdi and Van Hentenryck, Pascal},
  journal = {arXiv preprint arXiv:2605.18692},
  year    = {2026},
  url     = {https://arxiv.org/abs/2605.18692}
}
```

The exam instances come from [exam-scheduling-mip-generator](https://github.com/Joeyetinghan/exam-scheduling-mip-generator), the generator for Cornell's final-exam scheduling model (commit and seed in [`DATASHEET.md`](benchmark/DATASHEET.md#collection-process)). If you use them, please also cite:

```bibtex
@article{ye2026cornell,
  title   = {Cornell University Uses Integer Programming to Optimize Final Exam Scheduling},
  author  = {Ye, Tinghan and Jovine, Adam S. and Van Osselaer, Willem and Zhu, Qihan and Shmoys, David B.},
  journal = {INFORMS Journal on Applied Analytics},
  volume  = {56},
  number  = {2},
  pages   = {159--177},
  year    = {2026},
  publisher = {INFORMS}
}
```
