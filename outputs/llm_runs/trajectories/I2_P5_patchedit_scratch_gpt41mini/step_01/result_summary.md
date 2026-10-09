# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['x', 'block_enrollment', 'slot_load_cap']
- Edit summary: Add a capacity constraint limiting total enrollment on Day 2 slots to at most 4000 students due to proctor shortage.
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
- Objective: 7330.000000 -> 8559.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2314.0, 10: 2262.0, 11: 2310.0, 12: 2805.0, 13: 2820.0, 14: 2721.0, 15: 2961.0, 16: 3388.0, 17: 2698.0, 18: 2667.0, 2: 2346.0, 3: 2618.0, 4: 2137.0, 5: 1886.0, 6: 2676.0, 7: 2100.0, 8: 2309.0, 9: 2403.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2314.0, 10: 2262.0, 11: 2310.0, 12: 2805.0, 13: 2820.0, 14: 2721.0, 15: 2961.0, 16: 3388.0, 17: 2698.0, 18: 2667.0, 2: 2346.0, 3: 2618.0, 4: 2137.0, 5: 1886.0, 6: 2676.0, 7: 2100.0, 8: 2309.0, 9: 2403.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}