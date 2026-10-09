# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_virtual_slot', 'reserved_slots', 'virtual_blocks']
- Edit summary: Reserve the evening slot immediately before the final evening slot (the penultimate evening slot) so it cannot be assigned to a real exam block, in order to allow auditorium arrangements for graduation events.
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
- Objective: 2577.000000 -> 2578.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {'entity': 23}, 'update': {'name': 'reserved_slots', 'value': [21]}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[21], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 17, 'slots': [21]}}}, rhs_spec={21: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'block_seq', 'availability'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[23], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 17, 'slots': [23]}}}, rhs_spec={23: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'block_seq', 'availability'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}

## Chosen actions

- `UPDATE_PARAMETER` {'op': 'UPDATE_PARAMETER', 'target': {'name': 'reserved_slots'}, 'scope': {'entity': 23}, 'update': {'name': 'reserved_slots', 'value': [21]}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[21], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 17, 'slots': [21]}}}, rhs_spec={21: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'block_seq', 'availability'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}
- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[23], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 17, 'slots': [23]}}}, rhs_spec={23: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'block_seq', 'availability'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}