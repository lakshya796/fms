# Scenario profiles

A dispatcher-configured planning + fallback logic per operational pattern — milk run, long
haul, reefer, local delivery, or any custom pattern — matched automatically **per cluster**
during a greedy solve, instead of the dispatcher hand-tuning weights for every plan or every
load.

This sits on top of [DISPATCH-PLANNER-V2.md](DISPATCH-PLANNER-V2.md) §3 (named strategies), it
does not replace it. `DispatchPlan.objective` is still the one strategy the whole solve runs
under. A scenario profile only *narrows* that strategy for the clusters it matches — the same
way a photo filter sits on top of a base image rather than repainting it. A single plan can
therefore run a reefer cluster under a hold-for-review fallback and a local-delivery cluster
under a buy-on-the-market fallback in the same solve, which a plan-wide `strategy` alone
cannot express.

Model: `backend/dispatch/models.py` (`ScenarioProfile`, `SCENARIO_TYPES`,
`SCENARIO_FALLBACK_ACTIONS`). Matching + merge logic: `backend/dispatch/solver/scenarios.py`.
Solver integration: `backend/dispatch/solver/greedy.py` (`Cluster`, `_dispose`, `solve`).
Persistence: `backend/dispatch/solver/engine.py`. API: `ScenarioProfileViewSet`
(`backend/dispatch/views.py`).

---

## 1. Why per cluster, not per plan

`build_clusters` (`greedy.py`) already groups every task sharing one pickup place into one
`Cluster` before the solver touches it — that grouping *is* the "several drops on one pickup"
signal a milk run needs, with no new plumbing. A plan is not one operational pattern: the same
solve can be asked to place a multi-drop milk run, a single interstate long haul, and a
same-city reefer delivery, each of which wants different weights and a different answer to
"what happens if this can't be placed." Scenario profiles match independently against each
`Cluster`, so each gets its own answer within one solve.

## 2. The model

| Field | Meaning |
|---|---|
| `name` | Unique, dispatcher-facing (e.g. "Reefer"). |
| `scenario_type` | One of `milk_run`, `long_haul`, `reefer`, `local_delivery`, `custom` — a label for the UI, not itself a matching rule. |
| `priority` | Lower matches first when a cluster satisfies more than one active profile's criteria. Also the model's default ordering (`Meta.ordering = ["priority", "name"]`). |
| `active` | Inactive profiles are never matched or applied. |
| `match_temperature_classes` | e.g. `["chiller", "frozen"]`. Empty matches any temperature. |
| `match_min_distance_km` / `match_max_distance_km` | Bounds on `Cluster.distance_km` (below). |
| `match_min_drops` | Minimum stops sharing the one pickup — `2` is the milk-run signal. |
| `match_same_city_only` | Only match when the pickup and every drop share a city. |
| `base_strategy` | A preset name from `strategies.STRATEGY_PRESETS` — informational; what actually applies is the *plan's* strategy with this profile's overrides merged on, not a wholesale swap to this preset (see §4). |
| `weight_overrides` / `constraint_overrides` | Merged onto the plan's own strategy — same key/value shapes as `strategies.Strategy.weights` / `.constraints`. |
| `fallback_action` | What happens when a matched cluster cannot be placed on any route — §5. |
| `fallback_profile` | Only used when `fallback_action="relax"` — the profile to retry under. Self-reference is rejected (`ScenarioProfileSerializer.validate`). |

`DispatchTask.matched_scenario` (FK, nullable) records which profile — if any — shaped that
task's outcome, set by the solver and readable on every task after a solve.

## 3. Matching a cluster

`Cluster.__init__` (`greedy.py`) computes the matching inputs once, at construction:

- `temperature_classes` — the set of every task's `temperature_class` in the cluster.
- `same_city` — `True` only when the pickup has a city and every drop's city equals it.
- `distance_km` — the **furthest single drop** from the pickup, not a total route length
  (route sequencing, and therefore total distance, is not known until the solver has already
  chosen where this cluster lands — the furthest drop is a stable proxy that is known up
  front).

`scenarios.match_profile(cluster, profiles)` walks `profiles` in priority order (pass
`ScenarioProfile.objects.filter(active=True)` straight through — its default ordering is
already `priority, name`) and returns the first one where every criterion *actually set* on
the profile is satisfied. A criterion left unset (e.g. no `match_min_drops`) matches anything;
a profile with no criteria at all matches every cluster. At most one profile matches a given
cluster.

## 4. How overrides apply

`scenarios.effective_strategy(base_strategy, profile)` merges `profile.weight_overrides` and
`profile.constraint_overrides` onto the plan's own resolved `Strategy` — `{**base.weights,
**profile.weight_overrides}` — so an override changes only the keys it names; every other
weight and constraint keeps whatever the plan's strategy already set. Passing `profile=None`
returns `base_strategy` unchanged. This is what every feasibility check and cost calculation
in `_evaluate_cluster` runs under for a matched cluster instead of the plan's raw strategy.

Overrides are **not** re-validated against `strategies.DEFAULT_WEIGHTS` /
`DEFAULT_CONSTRAINTS` at merge time — that check already happened once, at save time, in
`ScenarioProfileSerializer.validate` (it reuses `strategies.resolve_strategy` itself, so a
profile's overrides are held to exactly the same key/type rules a solve request's overrides
are).

## 5. Fallback actions

When a matched cluster has no feasible route (or every feasible route costs more than the
outsource threshold), what happens next depends on `fallback_action`:

| Action | Effect | Resulting `DispatchTask.status` |
|---|---|---|
| `outsource` (default) | Bought on the spot market — the pre-existing behaviour, grouped into a `HireRequirement` per pickup lane. | `outsourced` |
| `relax` | One retry, immediately, under `fallback_profile`'s own effective strategy (still merged onto the *plan's* strategy — `fallback_profile` is just a different override set, not a different plan). If the retry also fails, falls through to that fallback profile's own `fallback_action`. | whatever the retry resolves to |
| `defer` | Pushed to the next plan rather than bought or held. No `HireRequirement` is created. | `deferred` |
| `hold` | Held for a dispatcher to look at by hand — e.g. a reefer load nothing can currently move, which the business would rather have a person check than buy from an unverified market vendor. No `HireRequirement` is created. | `held_for_review` |

`relax` only chains one hop: it retries under `fallback_profile`, and does not itself chase
*that* profile's `relax` (if any) any further. Two profiles are never allowed to name each
other as `fallback_profile` in a cycle — `ScenarioProfileSerializer.validate` blocks a profile
from being its own fallback; a longer cycle is prevented in practice by keeping `relax` a
single hop rather than by a graph check.

A cluster with no matched profile at all falls back to `outsource` — the behaviour before
scenario profiles existed.

**`hold` is the one exception to `allow_partial_service=False`.** A plan whose strategy sets
`constraints.allow_partial_service=False` normally refuses to solve at all rather than drop a
single task (`strategies.py`, `greedy._dispose`). A hold is a deliberate, visible stop, not a
silently lost load, so it is let through even then; every other disposition — including a
`relax` that ultimately resolves to `outsource` or `defer` — still has to honour that refusal
and raises `RuntimeError` if it would apply.

## 6. Scope: greedy only

Scenario profiles are a greedy-solver feature. `engine.solve_plan` fetches active profiles
unconditionally but only threads them into the two `greedy_solve(...)` call sites — never into
`ortools_solve(...)`. OR-Tools builds one global CP-SAT-style objective for the whole model; it
has no per-cluster hook to swap in a different weight vector mid-solve the way the greedy
construction's cluster-by-cluster loop does. A plan solved with `plan.solver="ortools"` runs
its one strategy uniformly, exactly as before scenario profiles existed.

## 7. The four seeded defaults

`python manage.py seed_scenario_profiles` (idempotent — matches by `name`, safe to re-run after
a dispatcher has already edited one; only the fields the command sets are reset, everything
else on the row is left alone):

| Name | Priority | Matches | Logic | Fallback |
|---|---|---|---|---|
| **Reefer** | 5 | `temperature_classes` ∈ {chiller, frozen} | Hard time windows, a heavy window-miss penalty. | `hold` — never bought from an unverified vendor without a human looking first. |
| **Milk Run** | 10 | ≥2 drops on one pickup | `max_utilisation` base, reward filling one truck over starting a second. | `outsource` |
| **Long Haul** | 20 | Furthest drop ≥400 km | A little more weight on driver time, a longer duty allowance for a relay. | `defer` — pushed to the next plan rather than forced through over-duty. |
| **Local Delivery** | 30 | Furthest drop ≤50 km, same city as pickup | `least_cost` base — cost-driven. | `outsource` — always fine to buy when cheaper. |

Priority is set so a load that could plausibly match two of these (e.g. a short, cold, single
drop) resolves to the more operationally specific one — Reefer before Local Delivery.

## 8. API

`ScenarioProfileViewSet`, registered at `/api/v1/dispatch/scenario-profiles/` — standard
list/create/retrieve/update/delete, `filter_fields = ["scenario_type", "active"]`,
`search_fields = ["name", "description"]`.

```
GET /api/v1/dispatch/scenario-profiles/{id}/preview/?plan={plan_id}
```

Runs the *matching* step only (no solve, nothing persisted) against a plan's currently
`pending` tasks, so a dispatcher can sanity-check a profile's criteria against real demand
before relying on it: `{"matched_clusters": N, "matched_tasks": N, "sample": [...]}`, sample
capped at 10 clusters.

Every `DispatchTaskSerializer` row carries `matched_scenario` (id) and `matched_scenario_name`
after a solve. `plan.summary` carries `outsourced_count` / `deferred_count` /
`held_for_review_count` (disaggregating what used to be one `outsourced` number) and
`scenario_breakdown` (`{profile_name_or_"none": task_count}`) so a dispatcher can see, per
plan, how much demand each profile actually touched. `plan.solver_status` reports the same
three counts in its one-line summary.
