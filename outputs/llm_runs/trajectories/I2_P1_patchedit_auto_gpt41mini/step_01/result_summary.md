# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_slots', 'reserved_virtual_slot', 'virtual_blocks']
- Edit summary: Reserve the evening slot immediately before the final evening slot for graduation auditorium setup.
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
- Objective: 7330.000000 -> 6207.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[23], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 19, 'slots': [23]}}}, rhs_spec={23: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'availability', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[23], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {23: {'fixed_block': 19, 'slots': [23]}}}, rhs_spec={23: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'availability', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}