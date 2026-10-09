# Exam Block Sequencing planner summary

- Delta: Due to an unexpected shortage of available proctors, limit the total number of students taking exams on Day 2 to a maximum of 4,000.
- Action kind: patch
- Supported ops: UPDATE_PARAMETER, UPDATE_BOUND, UPDATE_CONSTRAINT_RHS, UPDATE_CONSTRAINT_LHS, UPDATE_OBJECTIVE_COEFF, UPDATE_OBJECTIVE_WEIGHT, ADD_CONSTRAINT_FAMILY
- Relevant components: ['slot_load_cap', 'block_enrollment', 'slots_per_day']
- Edit summary: Add a day-level load cap limiting total enrolled students in Day 2 (slots 4, 5, 6) to at most 4,000.
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
- Objective: 4218.000000 -> 4150.000000
- Solve status: 9

## Candidate actions

- action_set `patch`
  - `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2232.0, 2: 2253.0, 3: 2558.0, 4: 2158.0, 5: 2338.0, 6: 2376.0, 7: 2405.0, 8: 2478.0, 9: 2295.0, 10: 2245.0, 11: 2294.0, 12: 2600.0, 13: 2823.0, 14: 2951.0, 15: 2634.0, 16: 2590.0, 17: 2835.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'block_seq', 'load_cap'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}

## Chosen actions

- `ADD_CONSTRAINT_FAMILY` {'op': 'ADD_CONSTRAINT_FAMILY', 'target': {'constraint': 'slot_load_cap'}, 'scope': {}, 'update': {'constraint': ConstraintFamily(name='slot_load_cap', index_set=['day_2'], lhs_spec={'kind': 'exam_x_aggregate', 'rows': {'day_2': {'slots': [4, 5, 6], 'block_weights': {1: 2232.0, 2: 2253.0, 3: 2558.0, 4: 2158.0, 5: 2338.0, 6: 2376.0, 7: 2405.0, 8: 2478.0, 9: 2295.0, 10: 2245.0, 11: 2294.0, 12: 2600.0, 13: 2823.0, 14: 2951.0, 15: 2634.0, 16: 2590.0, 17: 2835.0}}}}, rhs_spec={'day_2': 4000.0}, sense='<=', desc='Cap weighted enrollment across a selected slot set.', tags={'block_seq', 'load_cap'}, aliases=set(), metadata={'rule_kind': 'slot_load_cap_family'})}, 'notes': ''}