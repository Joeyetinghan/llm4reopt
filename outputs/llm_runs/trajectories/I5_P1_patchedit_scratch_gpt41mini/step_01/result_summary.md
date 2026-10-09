# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_virtual_slot', 'reserved_slots', 'virtual_blocks', 'slot_times', 'slots_per_day']
- Edit summary: Reserve the evening slot immediately before the final evening slot for auditorium setup.
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
- Objective: 2577.000000 -> 3490.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [20], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {20: {'fixed_block': 17, 'slots': [20]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {20: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [20, 21]}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [20, 21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {20: {'fixed_block': 17, 'slots': [20]}, 21: {'fixed_block': 18, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {20: 1.0, 21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [20], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {20: {'fixed_block': 17, 'slots': [20]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {20: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [20, 21]}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [20, 21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {20: {'fixed_block': 17, 'slots': [20]}, 21: {'fixed_block': 18, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {20: 1.0, 21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}