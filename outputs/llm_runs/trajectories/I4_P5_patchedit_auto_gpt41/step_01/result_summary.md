# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'block_enrollment', 'x', 'slots_per_day']
- Edit summary: Limit the total number of students taking exams on Day 2 to a maximum of 4,00 due to proctor shortage.
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
- Objective: 6009.000000 -> 5849.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2187.0, 2: 2465.0, 3: 2610.0, 4: 2161.0, 5: 2362.0, 6: 2289.0, 7: 2210.0, 8: 2473.0, 9: 2107.0, 10: 2483.0, 11: 2131.0, 12: 2310.0, 13: 2315.0, 14: 2457.0, 15: 2185.0, 16: 2149.0, 17: 3288.0, 18: 2530.0, 19: 2717.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'block_seq', 'load_cap'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2187.0, 2: 2465.0, 3: 2610.0, 4: 2161.0, 5: 2362.0, 6: 2289.0, 7: 2210.0, 8: 2473.0, 9: 2107.0, 10: 2483.0, 11: 2131.0, 12: 2310.0, 13: 2315.0, 14: 2457.0, 15: 2185.0, 16: 2149.0, 17: 3288.0, 18: 2530.0, 19: 2717.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'block_seq', 'load_cap'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}