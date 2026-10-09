# Exam Block Sequencing planner summary

- Delta: Reserve the evening slot immediately before the final evening slot so the staff can begin arranging the auditorium for graduation events.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['reserved_virtual_slot', 'slots_per_day', 'slot_times', 'virtual_blocks']
- Edit summary: Reserve the penultimate evening slot (slot 21) by assigning the smallest virtual block (19) to keep it empty for graduation setup.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 1
- Model retries: 0
- Strategy: warm+tuned
- Execution label: direct+heuristic+tuned
- Strategy policy: llm
- Toolbox plan: ['direct_warm_start', 'heuristic_warm_start', 'tuned_config']
- Strategy fallback used: True
- Objective: 7330.000000 -> 7336.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[21], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 19, 'slots': [21]}}}, rhs_spec={21: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'block_seq', 'availability'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'reserved_virtual_slot'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='reserved_virtual_slot', index_set=[21], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {21: {'fixed_block': 19, 'slots': [21]}}}, rhs_spec={21: 1.0}, sense='=', desc='Reserve selected slots by assigning them to deterministic virtual blocks.', tags={'block_seq', 'availability'}, aliases=set(), metadata={'rule_kind': 'reserved_virtual_slot_family'})}, 'notes': ''}