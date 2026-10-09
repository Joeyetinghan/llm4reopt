# Exam Block Sequencing planner summary

- Delta: The Student Assembly raised concerns about extreme stress; increase the penalty for having "three exams in 24 hours" to be 20 times that of a regular back-to-back.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['beta', 'gamma2', 'triple_in_24hr', 'b2b_other']
- Edit summary: Set the triple-in-24hr penalty weight (beta) to be 20x a regular back-to-back (gamma2), updating beta from 10.0 to 20.0.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 1
- Model retries: 0
- Strategy: tuned
- Execution label: heuristic+tuned
- Strategy policy: llm
- Toolbox plan: ['heuristic_warm_start', 'tuned_config']
- Strategy fallback used: False
- Objective: 7330.000000 -> 6834.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': "Interpret 'regular back-to-back' as gamma2 (other adjacent-slot pairs). Current gamma2 weight is 1.0, so beta set to 20*1.0 = 20.0."}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}

## Chosen actions

- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': "Interpret 'regular back-to-back' as gamma2 (other adjacent-slot pairs). Current gamma2 weight is 1.0, so beta set to 20*1.0 = 20.0."}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'beta'}, 'scope': {}, 'update': {'name': 'beta', 'value': 20.0}, 'notes': ''}