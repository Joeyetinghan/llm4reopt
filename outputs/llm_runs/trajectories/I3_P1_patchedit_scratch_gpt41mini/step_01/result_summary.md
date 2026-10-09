# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_virtual_slot', 'virtual_blocks', 'reserved_slots']
- Edit summary: Reserve the evening slot immediately before the final evening slot with a virtual block to block scheduling for setup before graduation.
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
- Objective: 4218.000000 -> 6157.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [22], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {22: {'fixed_block': 18, 'slots': [22]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {22: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [22], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {22: {'fixed_block': 18, 'slots': [22]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {22: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}