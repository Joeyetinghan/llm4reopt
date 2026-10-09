# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'x', 'block_enrollment']
- Edit summary: Add a cap of 4,00 students across all slots on Day 2 because of proctor shortage.
- Planner parse ok: True
- Planner output executable: True
- Planner failed semantically: False
- Model attempts: 1
- Model retries: 0
- Strategy: tuned
- Execution label: tuned
- Strategy policy: llm
- Toolbox plan: ['tuned_config']
- Strategy fallback used: False
- Objective: 4218.000000 -> 4223.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2232.0, 2: 2253.0, 3: 2558.0, 4: 2158.0, 5: 2338.0, 6: 2376.0, 7: 2405.0, 8: 2478.0, 9: 2295.0, 10: 2245.0, 11: 2294.0, 12: 2600.0, 13: 2823.0, 14: 2951.0, 15: 2634.0, 16: 2590.0, 17: 2835.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'load_cap', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2232.0, 2: 2253.0, 3: 2558.0, 4: 2158.0, 5: 2338.0, 6: 2376.0, 7: 2405.0, 8: 2478.0, 9: 2295.0, 10: 2245.0, 11: 2294.0, 12: 2600.0, 13: 2823.0, 14: 2951.0, 15: 2634.0, 16: 2590.0, 17: 2835.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'load_cap', 'block_seq'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}