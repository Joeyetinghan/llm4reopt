# Raw Instance Files

These are the instance generator's output files, one directory per instance. The framework builds the MIP from them ([`prepare_data`](../../README.md#running-reopt-llm)). The same instances, in one JSON format with their base schedules, are [`../instances/I1.json` … `I5.json`](../README.md); use those to work with the benchmark without a solver.

## Instance names

The directory and file names encode the instance size: `blockseq_n<exams>_blocks<real blocks>_slots<slots>_seed<seed>`.

| benchmark id | raw name |
| --- | --- |
| I1 | `blockseq_n544_blocks20_slots24_seed42` |
| I2 | `blockseq_n601_blocks18_slots24_seed42` |
| I3 | `blockseq_n553_blocks17_slots25_seed42` |
| I4 | `blockseq_n588_blocks19_slots24_seed42` |
| I5 | `blockseq_n539_blocks16_slots24_seed42` |

## Files

These files are the generator's output; its commit, seed and the paper to cite are in [`../DATASHEET.md`](../DATASHEET.md#collection-process).

| file | columns / fields | meaning |
| --- | --- | --- |
| `blockmap.csv` | `exam, block` | the block each exam belongs to (exam ids are anonymous integers) |
| `block_summary.csv` | `block, is_virtual, num_exams, block_enrollment, is_large_block` | per block: virtual flag, number of exams, total exam seats, and whether it contains an exam with more than 300 students |
| `pair_counts.csv` | `block_i, block_j, count` | students with an exam in both blocks. Listed in both orders (symmetric). The diagonal (`block_i = block_j`) counts students with two exams in the same block and is not used by the model. Missing pairs are 0. |
| `triplet_counts.csv` | `block_i, block_j, block_k, count` | students with exams in all three blocks, for ordered triples (repeated ids possible). Missing triples are 0. |
| `instance.json` | `all_blocks`, `virtual_blocks`, `large_blocks`, `early_slots`, `triple_day_start`, `triple_24_start`, `eve_morn_start`, `other_b2b_start`, `parameters` | block sets, the slots at which each penalty window starts, and `parameters`: the objective weights (`alpha`, `beta`, `gamma1`, `gamma2`, `delta`), `frontload_block_size_cutoff` (300) and `frontload_slot_cutoff` (21, the last early slot) |

