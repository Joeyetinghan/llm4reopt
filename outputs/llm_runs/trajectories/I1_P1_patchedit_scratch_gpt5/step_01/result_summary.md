# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_virtual_slot', 'slots_per_day', 'slot_times', 'virtual_blocks', 'reserved_slots']
- Edit summary: Reserve the penultimate evening slot (slot 21) by fixing virtual block 21 there to free it for graduation setup.
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
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 21, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}

## Result

- Validation/solve failed before a patch was chosen.