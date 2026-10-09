# Datasheet: Exam Re-Optimization Benchmark

Structured after *Datasheets for Datasets* (Gebru et al.). The dataset is the content of `benchmark/`; the paper's recorded runs, which come with it, are in `outputs/llm_runs/` and are covered here too. What the files contain and how they are scored is in [`README.md`](README.md); every field is defined in [`FIELDS.md`](FIELDS.md).

## Motivation

- **Purpose.** To evaluate methods that turn a natural-language change request into a modified optimization model and re-optimize a deployed solution, with a solver-free evaluator so that schedule quality can be scored without a MIP solver.
- **Creators.** The authors of *Democratizing Large-Scale Re-Optimization with LLM-Guided Model Patches* (arXiv:2605.18692). The benchmark is the paper's Cornell exam case study.

## Composition

- **Contents.** 5 instances with their base solutions, 30 tasks with gold edits and reference schedules ([`README.md`](README.md)), and 270 recorded LLM runs with their trajectories ([`outputs/llm_runs/`](../outputs/llm_runs/README.md)).
- **Privacy.** The instances are synthetic. They hold block-level aggregates only (block enrollment, exams per block, pair and triplet co-enrollment counts) plus an anonymous exam-to-block map with integer exam ids (`raw_instances/*/blockmap.csv`). There are no student-, course- or person-level records.
- **Labels.** The gold edits and reference schedules are the labels. They were produced by `problems/exam_block_seq/ground_truth/reference.py` and checked automatically with `reopt_exam.py` ([Scoring](README.md#scoring)).

## Collection process

- **Instance generation.** [exam-scheduling-mip-generator](https://github.com/Joeyetinghan/exam-scheduling-mip-generator) at commit `9a834d1920e9bba2ff58d8b0c632368c75e69db9`, seed 42, calibrated to the enrollment profiles of five Cornell terms (Spring 2022 to Spring 2024). The generator implements the block-sequencing model Cornell uses for its final exams (Ye et al., *INFORMS Journal on Applied Analytics*, 2026); please cite it along with the paper if you use the instances ([BibTeX](../README.md#citation)).
- **Change requests.** Six request templates (`problems/exam_block_seq/prompts/catalog.yaml`). Wording is fixed per prompt id, with instance-specific parameters (e.g. the P3 slot cutoff).
- **Runs.** One campaign (`paper_full_gpt_3600s`) of the repository's batch runner, made in April 2026. Each run's settings are in the run manifests ([`outputs/llm_runs/README.md`](../outputs/llm_runs/README.md#rerunning)).
- **Processing.** `instances/`, `tasks.jsonl`, `outputs/llm_runs/runs.jsonl`, `runs.csv` and the trajectories were assembled by the authors' internal export script, which is not released. It checks that every base and reference objective and every run's paper metrics are reproduced by `reopt_exam.py` and computes the runs' [`gold_*` fields](../outputs/llm_runs/FIELDS.md#re-score-under-the-gold-edit) with it. Trajectories were copied verbatim except for path rewriting (campaign directories → `outputs/llm_runs/trajectories/<run_id>`, the development repository's data paths → `benchmark/raw_instances/` and `outputs/solves/`, re-joining paths that aider wrapped across lines; the development repository's 30 per-instance reference scripts, which differed only in the instance id, → `problems/exam_block_seq/ground_truth/reference.py`, which holds the same edits) and removal of solver license lines and machine-specific file paths. Everything the script checks can be re-checked with `reopt_exam.py` and the data alone.

## Known limitations

- **References are not optimal**: they are 1-hour incumbents ([Scoring](README.md#scoring)).
- **Interpretation choices**: ambiguous requests were given one reading each ([interpretation notes](README.md#tasks)). Other readings are defensible, and a method that adopts one will be scored as violating or as optimizing a different objective.
- **The paper's columns describe the model each method built**, not the gold model ([two notions of feasibility](../outputs/llm_runs/README.md#runs)).
- **Selector fallback**: in 71 of the 180 runs with the strategy selector, the rule-based policy chose the strategy instead of the LLM (`selection_fallback_used` in [`outputs/llm_runs/FIELDS.md`](../outputs/llm_runs/FIELDS.md#re-optimization-strategy)); strategy comparisons should account for it.
- **One sample per configuration**: each method × model × task was run once.
- **Scale**: five instances from one institution's profile; results may not transfer to other timetabling settings.

## Uses

- Suitable: see [Ideas for use](README.md#ideas-for-use).
- Not suitable: inferring anything about real students or courses (the data is synthetic and aggregated), or claiming optimality from gaps to the reference.

## Distribution and maintenance

- **License.** See the [top-level README](../README.md#license).
- **Versioning.** `MANIFEST.json → version` follows semantic versioning; any change to tasks, gold edits, or scoring increments the version.
- **Issues.** Report problems through the repository's issue tracker.
