# Exam Block Sequencing planner summary

- Delta: Apply the following updates in this exact order: P4, then P2, then P1.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['p', 't', 'frontload_block_size_cutoff', 'frontload_slot_cutoff', 'frontload', 'reserved_virtual_slot', 'alpha', 'beta', 'gamma1', 'gamma2', 'delta']
- Edit summary: Apply updates P4, P2, P1 in sequence for exam block sequencing model.
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
- Objective: 2577.000000 -> 2710.000000
- Solve status: 9

## Candidate actions

- action_set `P6`
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'gamma1'}, 'scope': {}, 'update': {'weight': 1.0}, 'notes': ''}
  - `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'key': [4, 9], 'name': 'p', 'value': 365.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'key': [9, 4], 'name': 'p', 'value': 365.0}, 'notes': ''}
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [21]}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 17, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}

## Chosen actions

- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'gamma1'}, 'scope': {}, 'update': {'weight': 1.0}, 'notes': ''}
- `UPDATE_OBJECTIVE_WEIGHT` {'op': 'UPDATE_OBJECTIVE_WEIGHT', 'target': {'objective': 'beta'}, 'scope': {}, 'update': {'weight': 20.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [4, 9]}, 'update': {'key': [4, 9], 'name': 'p', 'value': 365.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'pair_counts'}, 'scope': {'entity': [9, 4]}, 'update': {'key': [9, 4], 'name': 'p', 'value': 365.0}, 'notes': ''}
- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {}, 'update': {'name': 'reserved_slots', 'value': [21]}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': {'aliases': [], 'desc': 'Reserve selected slots by assigning them to deterministic virtual blocks.', 'index_set': [21], 'lhs_spec': {'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 17, 'slots': [21]}}}, 'metadata': {'rule_kind': 'reserved_virtual_slot_family'}, 'name': 'reserved_virtual_slot', 'rhs_spec': {21: 1.0}, 'sense': '=', 'tags': ['availability', 'block_seq']}}, 'notes': ''}