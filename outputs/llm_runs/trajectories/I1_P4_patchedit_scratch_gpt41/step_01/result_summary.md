# Exam Block Sequencing planner summary

- Delta: The Student Assembly raised concerns about extreme stress; increase the penalty for having "three exams in 24 hours" to be 20 times that of a regular back-to-back.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['objective_component.beta', 'parameter.beta', 'objective_component.gamma1', 'parameter.gamma1', 'objective_component.gamma2', 'parameter.gamma2']
- Edit summary: Increase the triple-in-24-hours penalty weight so it is 20× the regular back-to-back penalty weight.
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
- Objective: 5338.000000 -> 6831.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': 'Update the objective component weight for triple-in-24hr penalty to 20.0.'}

## Chosen actions

- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}
- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': 'Update the objective component weight for triple-in-24hr penalty to 20.0.'}