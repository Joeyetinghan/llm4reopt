# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'x', 'block_enrollment', 'slots_per_day']
- Edit summary: Add a day-level load cap limiting total enrolled students on Day 2 (slots 4,5,6) to 4,000.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Failure stage: None
- Failure class: None
- Failure retryable: None
- Model attempts: None
- Model retries: None
- Strategy: scratch
- Execution label: scratch
- Strategy policy: manual
- Toolbox plan: []
- Strategy fallback used: False
- Objective before failure: 6009.000000
- Error: No incumbent solution available at status 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2187.0, 10: 2483.0, 11: 2131.0, 12: 2310.0, 13: 2315.0, 14: 2457.0, 15: 2185.0, 16: 2149.0, 17: 3288.0, 18: 2530.0, 19: 2717.0, 2: 2465.0, 3: 2610.0, 4: 2161.0, 5: 2362.0, 6: 2289.0, 7: 2210.0, 8: 2473.0, 9: 2107.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}

## Result

- Validation/solve failed before a patch was chosen.