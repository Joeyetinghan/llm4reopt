# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_slots', 'reserved_virtual_slot', 'virtual_blocks']
- Edit summary: Reserve the evening slot immediately before the final evening slot for auditorium setup by adding it to reserved_slots, which triggers a reserved_virtual_slot constraint.
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
- Objective: 6009.000000 -> 7891.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [23]}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [23], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 20, 'slots': [23]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {23: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [23], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 20, 'slots': [23]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {23: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}

## Chosen actions

- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [23]}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [23], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 20, 'slots': [23]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {23: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [23], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 20, 'slots': [23]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {23: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}