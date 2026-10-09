# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'slots_per_day', 'block_enrollment', 'x']
- Edit summary: Add a day-level load cap to limit total student enrollment on Day 2 to 4,00 across slots 4–6 due to proctor shortage.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 1
- Model retries: 0
- Strategy: warm+tuned
- Execution label: direct+heuristic+tuned
- Strategy policy: llm
- Toolbox plan: ['direct_warm_start', 'heuristic_warm_start', 'tuned_config']
- Strategy fallback used: True
- Objective: 2577.000000 -> 2570.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2353.0, 2: 2350.0, 3: 2325.0, 4: 2335.0, 5: 2283.0, 6: 2431.0, 7: 2509.0, 8: 2298.0, 9: 2083.0, 10: 2343.0, 11: 2709.0, 12: 2952.0, 13: 2791.0, 14: 2487.0, 15: 2980.0, 16: 3242.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'load_cap', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2353.0, 2: 2350.0, 3: 2325.0, 4: 2335.0, 5: 2283.0, 6: 2431.0, 7: 2509.0, 8: 2298.0, 9: 2083.0, 10: 2343.0, 11: 2709.0, 12: 2952.0, 13: 2791.0, 14: 2487.0, 15: 2980.0, 16: 3242.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'load_cap', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}