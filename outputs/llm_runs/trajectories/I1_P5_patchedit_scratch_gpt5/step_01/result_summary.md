# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'block_enrollment', 'x', 'slots_per_day']
- Edit summary: Add a day-level student load cap of 4,00 for Day 2 by capping total enrollment across slots 4–6.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 0
- Model retries: 0
- Strategy: scratch
- Execution label: scratch
- Strategy policy: manual
- Toolbox plan: []
- Strategy fallback used: False
- Objective: 5338.000000 -> 22311517.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2081.0, 10: 2312.0, 11: 2023.0, 12: 2012.0, 13: 1837.0, 14: 2232.0, 15: 2105.0, 16: 1841.0, 17: 2123.0, 18: 2468.0, 19: 2674.0, 2: 2027.0, 20: 2553.0, 3: 1803.0, 4: 1777.0, 5: 1989.0, 6: 2043.0, 7: 1955.0, 8: 1962.0, 9: 1770.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2081.0, 10: 2312.0, 11: 2023.0, 12: 2012.0, 13: 1837.0, 14: 2232.0, 15: 2105.0, 16: 1841.0, 17: 2123.0, 18: 2468.0, 19: 2674.0, 2: 2027.0, 20: 2553.0, 3: 1803.0, 4: 1777.0, 5: 1989.0, 6: 2043.0, 7: 1955.0, 8: 1962.0, 9: 1770.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}