# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'x', 'block_enrollment', 'slots_per_day']
- Edit summary: Add a day-level load cap to limit total enrollment across Day 2 (slots 4, 5, 6) to 4,00 students.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 1
- Model retries: 0
- Strategy: tuned
- Execution label: heuristic+tuned
- Strategy policy: llm
- Toolbox plan: ['heuristic_warm_start', 'tuned_config']
- Strategy fallback used: False
- Objective: 7330.000000 -> 6410.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2314.0, 2: 2346.0, 3: 2618.0, 4: 2137.0, 5: 1886.0, 6: 2676.0, 7: 2100.0, 8: 2309.0, 9: 2403.0, 10: 2262.0, 11: 2310.0, 12: 2805.0, 13: 2820.0, 14: 2721.0, 15: 2961.0, 16: 3388.0, 17: 2698.0, 18: 2667.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'load_cap', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2314.0, 2: 2346.0, 3: 2618.0, 4: 2137.0, 5: 1886.0, 6: 2676.0, 7: 2100.0, 8: 2309.0, 9: 2403.0, 10: 2262.0, 11: 2310.0, 12: 2805.0, 13: 2820.0, 14: 2721.0, 15: 2961.0, 16: 3388.0, 17: 2698.0, 18: 2667.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'load_cap', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}