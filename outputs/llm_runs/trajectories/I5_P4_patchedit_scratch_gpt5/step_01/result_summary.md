# Exam Block Sequencing planner summary

- Delta: The Student Assembly raised concerns about extreme stress; increase the penalty for having "three exams in 24 hours" to be 20 times that of a regular back-to-back.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['beta', 'gamma2', 'triple_in_24hr']
- Edit summary: Increase the triple-in-24hr penalty so its weight is 20x a regular back-to-back; set beta weight to 20.0.
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
- Objective: 2577.000000 -> 2642.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': 'Set triple-in-24hr penalty weight to 20x the regular back-to-back baseline (gamma2 currently 1.0).'}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}

## Chosen actions

- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': 'Set triple-in-24hr penalty weight to 20x the regular back-to-back baseline (gamma2 currently 1.0).'}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}