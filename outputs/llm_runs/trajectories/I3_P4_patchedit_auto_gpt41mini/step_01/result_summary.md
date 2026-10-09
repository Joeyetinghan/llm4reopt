# Exam Block Sequencing planner summary

- Delta: The Student Assembly raised concerns about extreme stress; increase the penalty for having "three exams in 24 hours" to be 20 times that of a regular back-to-back.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['parameter.delta', 'parameter.gamma2']
- Edit summary: Increase penalty weight delta for three exams in 24 hours to 20 times the regular back-to-back penalty gamma2
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 1
- Model retries: 0
- Strategy: warm+tuned
- Execution label: direct+heuristic+tuned
- Strategy policy: llm
- Toolbox plan: ['direct_warm_start', 'heuristic_warm_start', 'tuned_config']
- Strategy fallback used: False
- Objective: 4218.000000 -> 5951.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'delta'}, 'scope': {}, 'update': {'name': 'delta', 'value': 20.0}, 'notes': ''}

## Chosen actions

- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'delta'}, 'scope': {}, 'update': {'name': 'delta', 'value': 20.0}, 'notes': ''}