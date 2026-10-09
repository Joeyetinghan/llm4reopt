# Exam Block Sequencing planner summary

- Delta: Ensure all large exams with over 300 students are completed before the 20th time slot to allow teaching assistants sufficient grading time.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['frontload', 'early_slots', 'large_blocks', 'frontload_slot_cutoff']
- Edit summary: Force all large exams (>300 students) to finish by the 19th slot (before the 20th), ensuring large exams are only assigned to early slots via the frontload constraint.
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
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'early_slots'}, 'scope': {}, 'update': {'name': 'early_slots', 'value': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 'notes': ''}
  - `UPDATE_CONSTRAINT_LHS` {'op': 'UPDATE_CONSTRAINT_LHS', 'target': {'constraint': 'frontload'}, 'scope': {}, 'update': {'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {1: {'fixed_block': 1, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 10: {'fixed_block': 10, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 11: {'fixed_block': 11, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 12: {'fixed_block': 12, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 13: {'fixed_block': 13, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 14: {'fixed_block': 14, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 15: {'fixed_block': 15, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 16: {'fixed_block': 16, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 17: {'fixed_block': 17, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 18: {'fixed_block': 18, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 19: {'fixed_block': 19, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 2: {'fixed_block': 2, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 20: {'fixed_block': 20, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 3: {'fixed_block': 3, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 4: {'fixed_block': 4, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 5: {'fixed_block': 5, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 6: {'fixed_block': 6, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 7: {'fixed_block': 7, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 9: {'fixed_block': 9, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}}}}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'frontload_slot_cutoff'}, 'scope': {}, 'update': {'name': 'frontload_slot_cutoff', 'value': 19}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'early_slots'}, 'scope': {}, 'update': {'name': 'early_slots', 'value': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 'notes': ''}
  - `UPDATE_CONSTRAINT_LHS` {'op': 'UPDATE_CONSTRAINT_LHS', 'target': {'constraint': 'frontload'}, 'scope': {}, 'update': {'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {1: {'fixed_block': 1, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 10: {'fixed_block': 10, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 11: {'fixed_block': 11, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 12: {'fixed_block': 12, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 13: {'fixed_block': 13, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 14: {'fixed_block': 14, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 15: {'fixed_block': 15, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 16: {'fixed_block': 16, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 17: {'fixed_block': 17, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 18: {'fixed_block': 18, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 19: {'fixed_block': 19, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 2: {'fixed_block': 2, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 20: {'fixed_block': 20, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 3: {'fixed_block': 3, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 4: {'fixed_block': 4, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 5: {'fixed_block': 5, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 6: {'fixed_block': 6, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 7: {'fixed_block': 7, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}, 9: {'fixed_block': 9, 'slots': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]}}}}, 'notes': ''}

## Result

- Validation/solve failed before a patch was chosen.