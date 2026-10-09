# Exam Block Sequencing planner summary

- Delta: The Student Assembly raised concerns about extreme stress; increase the penalty for having "three exams in 24 hours" to be 20 times that of a regular back-to-back.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['objective_component:beta', 'parameter:beta', 'objective_component:gamma1', 'parameter:gamma1', 'objective_component:gamma2', 'parameter:gamma2']
- Edit summary: Increase the penalty for 'three exams in 24 hours' to 20 times that of a regular back-to-back, by adjusting the beta objective weight to 20.0.
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
- Objective before failure: 7330.000000
- Error: No incumbent solution available at status 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': 'Set the objective weight for three-in-24hr penalty (beta) to 20.0 to match new policy.'}

## Result

- Validation/solve failed before a patch was chosen.