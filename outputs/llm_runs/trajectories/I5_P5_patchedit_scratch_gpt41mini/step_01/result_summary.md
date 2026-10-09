# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'block_enrollment', 'x']
- Edit summary: Add enrollment cap constraint limiting total students on Day 2 to 4000
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
- Objective: 2577.000000 -> 3230.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2353.0, 10: 2343.0, 11: 2709.0, 12: 2952.0, 13: 2791.0, 14: 2487.0, 15: 2980.0, 16: 3242.0, 2: 2350.0, 3: 2325.0, 4: 2335.0, 5: 2283.0, 6: 2431.0, 7: 2509.0, 8: 2298.0, 9: 2083.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 1}, 'update': {'key': 1, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 2}, 'update': {'key': 2, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 3}, 'update': {'key': 3, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 4}, 'update': {'key': 4, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 5}, 'update': {'key': 5, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 6}, 'update': {'key': 6, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 7}, 'update': {'key': 7, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 8}, 'update': {'key': 8, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 9}, 'update': {'key': 9, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 10}, 'update': {'key': 10, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 11}, 'update': {'key': 11, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 12}, 'update': {'key': 12, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 13}, 'update': {'key': 13, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 14}, 'update': {'key': 14, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 15}, 'update': {'key': 15, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 16}, 'update': {'key': 16, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 17}, 'update': {'key': 17, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 18}, 'update': {'key': 18, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 19}, 'update': {'key': 19, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 20}, 'update': {'key': 20, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 21}, 'update': {'key': 21, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 22}, 'update': {'key': 22, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 23}, 'update': {'key': 23, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 24}, 'update': {'key': 24, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Cap weighted enrollment across a selected slot set.', 'index_set': ['day_2'], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {'day_2': {'block_weights': {1: 2353.0, 10: 2343.0, 11: 2709.0, 12: 2952.0, 13: 2791.0, 14: 2487.0, 15: 2980.0, 16: 3242.0, 2: 2350.0, 3: 2325.0, 4: 2335.0, 5: 2283.0, 6: 2431.0, 7: 2509.0, 8: 2298.0, 9: 2083.0}, 'slots': [4, 5, 6]}}}, 'metadata': {'rule_kind': 'slot_load_cap_family'}, 'name': 'slot_load_cap', 'rhs_spec': {'day_2': 4000.0}, 'sense': '<=', 'tags': ['block_seq', 'load_cap']}}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 1}, 'update': {'key': 1, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 2}, 'update': {'key': 2, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 3}, 'update': {'key': 3, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 4}, 'update': {'key': 4, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 5}, 'update': {'key': 5, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 6}, 'update': {'key': 6, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 7}, 'update': {'key': 7, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 8}, 'update': {'key': 8, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 9}, 'update': {'key': 9, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 10}, 'update': {'key': 10, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 11}, 'update': {'key': 11, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 12}, 'update': {'key': 12, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 13}, 'update': {'key': 13, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 14}, 'update': {'key': 14, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 15}, 'update': {'key': 15, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 16}, 'update': {'key': 16, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 17}, 'update': {'key': 17, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 18}, 'update': {'key': 18, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 19}, 'update': {'key': 19, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 20}, 'update': {'key': 20, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 21}, 'update': {'key': 21, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 22}, 'update': {'key': 22, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 23}, 'update': {'key': 23, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'block_enrollment'}, 'scope': {'entity': 24}, 'update': {'key': 24, 'name': 'block_enrollment', 'value': 0.0}, 'notes': ''}