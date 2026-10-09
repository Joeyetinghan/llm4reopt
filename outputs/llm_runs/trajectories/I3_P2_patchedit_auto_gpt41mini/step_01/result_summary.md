# Exam Block Sequencing planner summary

- Delta: Increase the pairwise co-enrollment count between Block 4 and Block 9 by 120 students due to late add/drop changes.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['pair_counts']
- Edit summary: Increase the pairwise co-enrollment count between Block 4 and Block 9 by 120 students.
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
- Objective: 4218.000000 -> 4003.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'name': 'p', 'key': (4, 9), 'value': 386.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'name': 'p', 'key': (9, 4), 'value': 386.0}, 'notes': ''}

## Chosen actions

- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'name': 'p', 'key': (4, 9), 'value': 386.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'name': 'p', 'key': (9, 4), 'value': 386.0}, 'notes': ''}