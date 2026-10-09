# Exam Block Sequencing planner summary

- Delta: Increase the pairwise co-enrollment count between Block 4 and Block 9 by 120 students due to late add/drop changes.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['pair_counts']
- Edit summary: Increase the pairwise co-enrollment count between Block 4 and Block 9 by 120 students in both directions.
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
- Objective before failure: 5338.000000
- Error: No incumbent solution available at status 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'key': [4, 9], 'name': 'p', 'value': 271.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'key': [9, 4], 'name': 'p', 'value': 271.0}, 'notes': ''}

## Result

- Validation/solve failed before a patch was chosen.