# Exam Block Sequencing planner summary

- Delta: Apply the following updates in this exact order: P4, then P2, then P1.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: []
- Edit summary: Apply (1) a patch to reserved slots or virtual block reservation, (2) a patch to frontloading/early slots, then (3) a patch to block or slot assignments, following canonical sequence P4->P2->P1 as requested. However, without delta details for P4, P2, and P1, unable to emit executable patches.
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
- Objective: 6009.000000 -> 8500.000000
- Solve status: 9

## Candidate actions

- action_set `P6`
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'gamma1'}, 'scope': {}, 'update': {'weight': 1.0}, 'notes': ''}
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'key': [4, 9], 'name': 'p', 'value': 341.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'key': [9, 4], 'name': 'p', 'value': 341.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [21]}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 20, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}

## Chosen actions

- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'gamma1'}, 'scope': {}, 'update': {'weight': 1.0}, 'notes': ''}
- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'key': [4, 9], 'name': 'p', 'value': 341.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'key': [9, 4], 'name': 'p', 'value': 341.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [21]}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 20, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}