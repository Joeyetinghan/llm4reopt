# Transportation Problem Context

You are helping an operations planner evaluate changes to a transportation plan.

- This is a classical transportation model: a single commodity is shipped from plants to customers.
- The model is about shipment quantities only. Inventory timing, vehicle routing, and multi-period effects are out of scope.
- Delta requests typically describe changes in plant capacity, customer demand, route cost, or route availability.

## Basic Assumptions

- Plants are sources with limited outbound supply.
- Customers are sinks with required inbound demand.
- Each plant-customer lane can carry a nonnegative shipment flow.
- The packaged instance data is provided directly through `plants`, `customers`, `supply`, `demand`, and `costs`.
- Identifiers such as `P1` and `C2` should be used directly rather than guessed from reformulated constraint names.

## Objective

The objective minimizes total transportation cost across all plant-customer lanes.

## Core Model View

- `flows[i,j]` is the shipment quantity from plant `i` to customer `j`.
- `supply_constraints[i]` limit total outbound flow from plant `i`.
- `demand_constraints[j]` require enough inbound flow to satisfy customer `j`.
- `transport_cost` is the linear objective component using the per-lane costs.

## Semantic Grounding Rules

- Requests about plant capacity usually change `supply` or the RHS of `supply_constraints`.
- Requests about customer demand usually change `demand` or the RHS of `demand_constraints`.
- Requests about route costs usually change `costs` or the corresponding objective coefficient in `transport_cost`.
- Requests about disabling or limiting a lane usually change bounds on `flows[i,j]`.
- References to plants, customers, and lanes should be preserved explicitly in the interpreted edit.
