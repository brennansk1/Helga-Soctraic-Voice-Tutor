# PHX Install Scheduling Skill — Design Review & Optimization Roadmap

Review of the proposed `skill.md` pipeline (5-queue pull → enrichment → greedy pairing →
day-by-day output), with the planned migration from Google Maps Distance Matrix to a
self-hosted OSRM instance with downloaded OSM extracts.

**Bottom line:** the *information gathering* half (Phases 1–2) is sound and mostly needs
SQL hardening. The *optimization* half (Phase 3) will leave a lot of efficiency on the
table because it is a greedy, day-by-day, panel-count heuristic. Moving to OSRM is not
just a cost swap — it removes the constraint that forced the greedy design in the first
place, and it should trigger a rewrite of the optimizer, not a find-and-replace of the
distance function.

---

## 1. Ranked list of changes, by payoff

| # | Change | Why it matters | Effort |
|---|--------|----------------|--------|
| 1 | Replace panel-count capacity with a **crew-hours duration model** fit from historical `project_event` actuals | Panels is a weak proxy for time. Array count, MPU, roof type and story count swing a job by hours. Every downstream decision inherits this error. | M |
| 2 | Replace the greedy fill with an **OR-Tools VRP solve over the whole horizon** | Greedy day-by-day is typically 20–35% worse on drive time and cannot undo an early bad pick. | L |
| 3 | Use OSRM **`/table`** for the full N×N matrix in one call; delete the pairwise loop and the clustering heuristic | Pairwise + `sleep(0.1)` at 200 jobs = ~20k calls, ~33 min of sleep alone. OSRM `/table` does the same matrix in well under a second. | S |
| 4 | Add **crew home/yard depots** — model first and last leg | Currently unmodeled. Typically 45–90 min/crew/day of real, invisible drive time. | S |
| 5 | Make age a **soft cost in the objective**, not a hard sort order | Hard "oldest first" drags crews across the state. Age belongs in the drop penalty. | S |
| 6 | **Geocode once and persist** lat/lon on the project record | OSRM takes coordinates, not addresses. Addresses don't move — geocoding per run is waste and a failure mode. | S |
| 7 | Model MPU/TDR/Tap Box as **honest service time**, not "max 1 per day" | "Max 1/day" still permits a full panel load alongside a 6-hour electrical job. | S |
| 8 | **Backtest** against last quarter's actual schedule before rollout | Without a baseline you cannot tell whether the optimizer helped. | M |
| 9 | Fix the fail-open bugs in travel-time checks and matrix keying | Silent wrong answers, not crashes. See §6. | S |
| 10 | Rolling re-optimization with a **frozen commit window** | Prevents re-solving from thrashing appointments already promised to customers. | M |

---

## 2. OSRM migration — concrete guidance

### 2.1 OSRM does not geocode

This is the first thing that bites people moving off Google. `/table` and `/route` take
`lon,lat` coordinates only. You need a separate geocoding step.

Options, best first:

- **Persist what you already have.** If any project already has lat/lon from a prior
  Google call, a design tool, or the CRM, store it and never geocode again.
- **Local Nominatim** built from the same `.osm.pbf` extracts. Fully offline, no quota.
  Rooftop accuracy on US residential addresses is mediocre in rural areas — expect
  ~85–92% usable, with the remainder falling back to street centroid or ZIP centroid.
- **One-time paid geocode, cached forever.** Geocoding is ~$5/1000 and an address is
  geocoded once in its lifetime. For a few thousand backlog projects this is a rounding
  error and gives rooftop quality. Pragmatically this is often the right call even in an
  otherwise-offline stack.

Store on the project (or a sidecar table):

```sql
ALTER TABLE project ADD COLUMN latitude   numeric(9,6);
ALTER TABLE project ADD COLUMN longitude  numeric(9,6);
ALTER TABLE project ADD COLUMN geocode_precision text;  -- rooftop | interpolated | street | zip
ALTER TABLE project ADD COLUMN geocode_snapped_m numeric;
ALTER TABLE project ADD COLUMN geocoded_at timestamptz;
```

**Validate every geocode with OSRM `/nearest`.** If the returned snap distance exceeds
~150 m, the point is not near a routable road and the whole matrix row is suspect — flag
it for manual review rather than scheduling against it.

```python
def validate_geocode(lon, lat, osrm="http://osrm:5000"):
    r = requests.get(f"{osrm}/nearest/v1/driving/{lon},{lat}", timeout=5).json()
    if r.get("code") != "Ok":
        return False, None
    snapped_m = r["waypoints"][0]["distance"]
    return snapped_m <= 150.0, snapped_m
```

Precision tiers should carry a confidence penalty into scheduling: a `zip`-precision job
can be off by 10 miles, so either exclude it from tight same-day pairings or inflate its
travel estimate.

### 2.2 Build and serve the graph

```bash
# One extract per state you operate in (Geofabrik).
wget https://download.geofabrik.de/north-america/us/virginia-latest.osm.pbf
wget https://download.geofabrik.de/north-america/us/ohio-latest.osm.pbf
wget https://download.geofabrik.de/north-america/us/pennsylvania-latest.osm.pbf

# Merge if crews ever cross state lines — otherwise routes near borders are wrong.
osmium merge virginia-latest.osm.pbf ohio-latest.osm.pbf pennsylvania-latest.osm.pbf \
  -o phx-region.osm.pbf

# Contraction Hierarchies: fastest for pure table queries.
osrm-extract  -p /opt/car.lua phx-region.osm.pbf
osrm-contract phx-region.osrm

# Serve. THE DEFAULT max-table-size IS 100 — you MUST raise it.
osrm-routed --algorithm ch --max-table-size 10000 phx-region.osrm
```

Two pitfalls worth calling out:

- **`--max-table-size` defaults to 100.** With 300 jobs your `/table` call returns a
  `TooBig` error, not a truncated result. Set it above your realistic max job count.
- **CH vs MLD.** Use CH (`osrm-contract`) if the graph is static — it gives the fastest
  table queries. Use MLD (`osrm-partition` + `osrm-customize`) only if you intend to push
  custom segment speeds (see §2.4), because MLD re-customizes in seconds where CH requires
  a full re-contract.

Region extracts are small: a three-state CH graph is roughly 1–2 GB resident. Refresh the
extracts quarterly; new subdivisions are exactly where your crews get lost.

### 2.3 One call, whole matrix

Replace the entire pairwise `distance_calculator.py` with this:

```python
import requests
from typing import Sequence, Tuple

OSRM = "http://osrm:5000"

def travel_matrix(coords: Sequence[Tuple[float, float]]):
    """
    coords: [(lon, lat), ...] in job order.
    Returns (durations_sec, distances_m) as N x N lists.
    """
    locs = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
    r = requests.get(
        f"{OSRM}/table/v1/driving/{locs}",
        params={"annotations": "duration,distance"},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM table failed: {data.get('code')} {data.get('message')}")
    return data["durations"], data["distances"]
```

If N exceeds `--max-table-size`, chunk with the `sources=` / `destinations=` params and
stitch the blocks — the API supports asymmetric sub-matrices specifically for this.

Note the matrix is **asymmetric** (one-way streets, ramps, divided highways). The current
design stores only `i < j` pairs and looks up unsorted keys, which is both a cache-miss bug
and a correctness assumption that does not hold. Keep the full N×N.

### 2.4 OSRM has no traffic — correct for it

Vanilla OSRM returns **free-flow** durations from `car.lua` max speeds. Crews roll at
06:30–07:00 into metro areas. Uncorrected, you will systematically under-estimate morning
drives and over-book every day.

Two ways to fix, in order of pragmatism:

**(a) Empirical calibration.** You already have ground truth: `project_event.start_time`
versus the prior job's `end_time`, plus crew GPS if the fleet is tracked. Fit a small
multiplier table:

```
factor(hour_of_day, urban_rural) — e.g.
  06:00–09:00 urban   1.35
  06:00–09:00 rural   1.10
  09:00–15:00 urban   1.15
  09:00–15:00 rural   1.05
  15:00–18:00 urban   1.30
```

Apply the factor to the OSRM duration when building the solver's cost matrix. This takes an
afternoon and captures most of the error.

**(b) Segment speed files.** Build with MLD and feed observed speeds:

```bash
osrm-customize phx-region.osrm --segment-speed-file observed_speeds.csv
```

Higher fidelity, much more plumbing. Only worth it once (a) is in place and measured.

Separately, add a **fixed last-mile constant** of roughly 5–8 minutes per stop, outside of
drive time, covering driveway approach, parking the truck and trailer, and staging. OSRM
routes to the road centerline; it knows nothing about a 400-foot gravel driveway. Rural VA
and OH are full of these.

Also consider a **loaded-truck profile.** Crews are not driving sedans. Either clone
`car.lua` with lower `max_speed` values and any weight/height restrictions your trailers
care about, or apply a flat 1.10–1.15 factor as a first approximation.

### 2.5 Fail gracefully

```python
def travel_matrix_with_fallback(coords):
    try:
        return travel_matrix(coords)
    except Exception as e:
        log.error("OSRM unavailable, falling back to haversine: %s", e)
        # Circuity factor ~1.3 approximates road distance from straight-line in the
        # eastern US; assume 40 mph average. Degraded, but the pipeline still runs.
        return haversine_matrix(coords, circuity=1.3, avg_mph=40.0)
```

The current code returns `(None, None)` on API failure and `_check_travel_time` then
returns `True` — which means an API outage silently permits *every* pairing regardless of
distance. Fail closed or fall back explicitly; never fail open on a constraint check.

### 2.6 Caching becomes mostly unnecessary

With Google, a distance cache was essential. With local OSRM a 500×500 table is
sub-second, so caching mainly adds staleness risk. Cache the *geocodes* — which are
expensive and permanent — and recompute the matrix every run.

---

## 3. Replace the greedy heuristic with a real VRP solve

The clustering + nearest-fit design in Phase 3 exists because distance lookups were
expensive. Once they are free, that constraint is gone and you should model the problem as
what it actually is: a **multi-day capacitated vehicle routing problem with time windows,
skill constraints, and optional (droppable) jobs**.

### 3.1 Why greedy costs you

- **Day-by-day is myopic.** Solving Monday in isolation, oldest-job-first, sends a crew to
  a far region for one aged job; Tuesday inherits a worse remaining set. Solving the full
  week jointly lets that aged job pair with three neighbors on Thursday.
- **Order-dependent clustering.** `cluster_jobs_by_distance` seeds on whatever job comes
  first and absorbs anything within 50 miles of the *seed*. Cluster diameter is unbounded
  — two members can be 100 miles apart. It is also non-deterministic across runs if input
  order changes.
- **No backtracking.** Once a crew-day is filled, nothing reconsiders it.
- **Hard age sort fights geography.** Age should be a cost, not a constraint.

### 3.2 Model specification

**Vehicles = crew × day.** Eight crews over a five-day horizon is 40 vehicles. Each has
its own start and end depot (the crew's home or yard) and its own workday length.

**Dimensions:**

| Dimension | Per-node demand | Capacity | Purpose |
|-----------|-----------------|----------|---------|
| `Time` | service_minutes(job) | crew workday minutes (e.g. 600) | The real constraint |
| `Heavy` | 1 if MPU/TDR/TapBox else 0 | 1 | At most one heavy electrical job |
| `Panels` | panel count | crew max panels | Secondary safety cap only |

Transit callback for `Time` = `osrm_duration × traffic_factor + last_mile_constant +
service_minutes(from_node)`.

**Skills** via `routing.SetAllowedVehiclesForIndex(vehicle_ids, node)` — a hot-work job is
only allowed on crews carrying that certification.

**Droppable jobs** via `AddDisjunction([node], penalty)`. This is where age lives:

```python
penalty = BASE_DROP_PENALTY + AGE_WEIGHT * min(days_since_sold, AGE_CAP)
```

The solver then *chooses* to schedule old jobs because dropping them is expensive, while
still being free to defer an old job if servicing it costs three hours of drive time and
strands two other jobs. That is exactly the tradeoff a human dispatcher makes intuitively.

**Critical tuning note:** `BASE_DROP_PENALTY` must exceed the largest plausible routing
cost of inserting any single job, or the solver will happily drop jobs to save drive time.
A safe starting rule is 3–5× the cost of the longest reasonable detour.

**Existing appointments** already promised to customers are hard-pinned: restrict them to
their assigned crew-day vehicle and set a narrow time window equal to the booked slot.
They then correctly consume capacity and pull nearby jobs toward them.

### 3.3 Skeleton

```python
from ortools.constraint_solver import routing_enums_pb2, pywrapcp

def solve(jobs, crew_days, matrix_sec, service_min, starts, ends):
    n = len(jobs) + len(starts)          # jobs + depot nodes
    mgr = pywrapcp.RoutingIndexManager(n, len(crew_days), starts, ends)
    routing = pywrapcp.RoutingModel(mgr)

    def time_cb(i, j):
        a, b = mgr.IndexToNode(i), mgr.IndexToNode(j)
        return int(matrix_sec[a][b] / 60 * TRAFFIC[a][b]) + LAST_MILE + service_min[a]

    tc = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(tc)
    routing.AddDimension(tc, 0, MAX_WORKDAY_MIN, True, "Time")

    def heavy_cb(i):
        return 1 if jobs[mgr.IndexToNode(i)].is_heavy else 0
    hc = routing.RegisterUnaryTransitCallback(heavy_cb)
    routing.AddDimensionWithVehicleCapacity(hc, 0, [1] * len(crew_days), True, "Heavy")

    for k, job in enumerate(jobs):
        idx = mgr.NodeToIndex(k)
        routing.AddDisjunction([idx], BASE_DROP + AGE_W * min(job.age_days, AGE_CAP))
        if job.required_skill:
            routing.SetAllowedVehiclesForIndex(
                [v for v, cd in enumerate(crew_days)
                 if job.required_skill in cd.crew.skills], idx)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(120)
    return routing.SolveWithParameters(params)
```

A 120-second time limit on a few hundred jobs lands very close to optimal. This is a batch
job run once a day — spend the two minutes.

### 3.4 Objective, stated honestly

The current spec says "fill each crew's panel capacity as close to 100% as possible."
That is the wrong target. Running crews at 100% of modeled capacity guarantees overruns,
because the model has variance and reality has rain. What you actually want to minimize:

```
  drop_penalty(unscheduled jobs, weighted by age)
+ w_travel  × total drive minutes
+ w_ot      × minutes over standard workday
```

Target roughly **85–90% of modeled capacity** as the planned load. The remaining buffer is
what absorbs a stuck permit, a bad roof, or traffic — and it is the difference between a
schedule that survives contact with Monday and one that cascades.

---

## 4. The duration model — highest-leverage data work

`remaining_panels -= 3  # estimate 3 panels for correction` is the tell. Everything
downstream is only as good as this estimate, and you have the data to do much better.

Fit from history:

```sql
SELECT pe.project_id,
       EXTRACT(EPOCH FROM (pe.end_time - pe.start_time)) / 60 AS actual_minutes
FROM project_event pe
JOIN object_status os ON pe.object_status_id = os.id
WHERE pe.archived = false
  AND pe.end_time IS NOT NULL
  AND os.object_status IN ('Completed', 'Complete')
  AND pe.start_time >= now() - interval '18 months';
```

Join against panels, arrays, MPU/TDR flags, roof type, story count, and job type, then fit
something deliberately simple and interpretable:

```
minutes = base
        + b_panel  × panels
        + b_array  × arrays          -- per-array setup is real and often underrated
        + b_mpu    × has_mpu
        + b_story  × (stories - 1)
        + b_type   × job_type
```

A linear model with a handful of terms will beat panel-count-as-proxy substantially, and
crews can sanity-check the coefficients — which matters more for adoption than a marginal
accuracy gain from a black box.

Then **track residuals per crew.** Some crews are consistently 20% faster. Feeding a
per-crew speed factor back in is a genuine efficiency gain and it surfaces training needs.

Do this for corrections too — bucket by `correction_desc` keywords (CT polarity, inverter
swap, conduit rework) and use the bucket median rather than a flat guess.

---

## 5. Constraints the current model is missing

| Missing | Impact | Fix |
|---------|--------|-----|
| **Crew home/depot locations** | First and last leg unmodeled; commonly 45–90 min/day of invisible drive | Add lat/lon per crew; use as VRP start/end |
| **Lunch and breaks** | 30–45 min/day of phantom capacity | Subtract from workday, or model as a break with OR-Tools `SetBreakIntervalsOfVehicle` |
| **Crew PTO, holidays, training** | Schedules jobs to crews who aren't there | Availability calendar per crew-day; skip those vehicles |
| **Customer time windows** | "Homeowner must be present after 3pm" is common and ignored | `Time` dimension `CumulVar(idx).SetRange(lo, hi)` |
| **Utility / inspector windows** | Missed windows cause re-visits — the most expensive failure mode | Same mechanism, hard windows |
| **Two-visit MPU/TDR jobs** | "Crew stays for reconnect" means a full-day block, not "1 per day" | Honest service time (e.g. 360 min) |
| **Weather** | Rain days cascade | Reserve buffer capacity; re-solve daily |
| **Material availability** | Scheduling a job whose panels haven't shipped | Gate at the queue-pull stage |
| **Max consecutive long days** | Crew burnout, overtime cost | Soft constraint across the horizon |

---

## 6. Bugs in the code as written

1. **`_check_travel_time` fails open.** Returns `True` when `minutes is None`, so an API
   failure permits every pairing. Fail closed.
2. **Redundant API calls.** `cluster_jobs_by_distance` calls `get_distance_miles` in its
   inner loop after `build_distance_matrix` already computed everything. Roughly doubles
   an already-quadratic cost.
3. **Matrix key mismatch.** `build_distance_matrix` stores `(job1_id, job2_id)` for
   `i < j`; the clustering code builds `tuple(sorted([...]))`. These agree only
   accidentally, and the symmetric assumption is wrong for road networks anyway.
4. **Cost and time at scale.** 200 jobs → 19,900 pairs. At Google's $5/1000 elements that
   is ~$100 per run, plus 33 minutes of `sleep(0.1)` alone. This is the entire reason to
   move to OSRM.
5. **Unbounded cluster diameter.** Greedy seeded clustering does not enforce transitivity;
   two cluster members can be arbitrarily far apart.
6. **Arbitrary correction sizing.** `remaining_panels -= 3` — see §4.
7. **No idempotency or run log.** Re-running produces a different schedule with no record
   of what changed or why. Persist each run with its inputs and parameters.
8. **API key in source.** `GOOGLE_MAPS_API_KEY = "YOUR_API_KEY"` — moot after the OSRM
   move, but the pattern should not survive into the OSRM config either.

---

## 7. SQL issues in Phase 1

**Wrong join.** In 1.1:

```sql
JOIN object_status os ON wqa.work_queue_type_id = os.id  -- or appropriate status join
```

This joins a work-queue-type id against an object-status id. It will produce rows — just
meaningless ones. Resolve the actual status column before shipping; a silently wrong join
is worse than a missing column.

**Hardcoded queue IDs.** `IN (11, 172, 74, 73, 67)  -- UPDATE THESE IDs` is a live
foot-gun. Resolve by name in a CTE so it cannot drift:

```sql
WITH target_queues AS (
  SELECT id FROM work_queue
  WHERE archived = false
    AND work_queue_name IN (
      'Ready to Schedule Install (event)',
      'Ready to Schedule Funding Correction Work',
      'Ready to Schedule Return Visit',
      'Ready to Schedule Inspection Correction Work (S)',
      'Ready to Schedule Service Work'
    )
)
SELECT ... FROM work_queue_activity wqa
WHERE wqa.work_queue_id IN (SELECT id FROM target_queues)
```

Assert the CTE returns exactly 5 rows and fail loudly otherwise — a renamed queue should
stop the pipeline, not silently shrink the job pool.

**Tap Box / MPU detects presence, not truth.** In 1.2D:

```sql
MAX(CASE WHEN ppbce.object_section_component_id IN (...) THEN 'Tap Box' END)
```

This flags any project that merely *has* the component entry, including one whose value is
`false` or `0`. Test the value:

```sql
MAX(CASE WHEN ppbce.object_section_component_id IN (...)
          AND COALESCE(ppbce.bool_value, ppbce.int_value > 0, false)
         THEN 'Tap Box' END)
```

Over-flagging here directly wastes capacity, since heavy jobs are capped at one per crew-day.

**Panel-count fallback is unspecified.** 1.2A pulls both `designed_panels` and
`proposed_panels` but never says which wins. Make it explicit —
`COALESCE(designed_panels, proposed_panels)` — and count how often you fall back. A job
with no panel count at all must be excluded from auto-scheduling, not defaulted to zero.

**Four round trips per batch.** Queries A–D can be a single CTE query. Minor at current
scale, but it removes four failure points.

---

## 8. Measure it, or you won't know

Before rollout, replay the optimizer against a completed quarter and compare against what
was actually scheduled:

| Metric | Why |
|--------|-----|
| Drive minutes per crew-day | The headline number OSRM + VRP should move |
| Panels per crew-day | Throughput |
| Modeled vs actual capacity utilization | Is the duration model honest? |
| Mean and p90 job age at scheduling | Is the aging policy working? |
| Jobs aged > 30 days | The backlog tail, where customer complaints live |
| On-time completion rate | Are you overloading crews? |
| Re-visit / callback rate | Overloading shows up here first |

Run the optimizer in **shadow mode** for two weeks: generate the schedule, let dispatch
schedule manually as usual, and diff. Where the human overrode the optimizer, ask why —
those answers are the constraints missing from §5. This is the fastest way to find them,
and it builds dispatcher trust before you ask them to act on the output.

---

## 9. Suggested rollout

1. **Week 1** — Geocode backfill + persist; stand up OSRM; validate with `/nearest`;
   replace the distance module with `/table`. Nothing else changes. Immediate: runtime
   drops from ~30 min to seconds, cost to zero.
2. **Week 2** — Fit the duration model from `project_event` history. Add crew depots,
   lunch, and availability. Still greedy, but now on honest inputs — this alone is a
   meaningful gain.
3. **Weeks 3–4** — OR-Tools VRP over the full horizon, replacing the greedy fill and the
   clustering heuristic. Backtest against last quarter.
4. **Weeks 5–6** — Shadow mode. Collect override reasons; fold them in as constraints.
5. **Week 7+** — Rolling daily re-optimization with days 1–2 frozen as committed, days
   3–10 free to move.

---

## 10. Revised configuration

| Parameter | Default | Notes |
|-----------|---------|-------|
| `osrm_url` | `http://osrm:5000` | Replaces `google_maps_api_key` |
| `osrm_max_table_size` | `10000` | Must exceed max job count; default of 100 will error |
| `traffic_factor_table` | calibrated | Per hour-of-day × urban/rural, from actuals |
| `last_mile_minutes` | `6` | Driveway, park, stage — outside OSRM's model |
| `workday_minutes` | `600` | Before lunch deduction |
| `lunch_minutes` | `40` | |
| `target_utilization` | `0.87` | Plan to this, not 1.0 |
| `solver_time_limit_sec` | `120` | Batch job — spend it |
| `base_drop_penalty` | `20000` | Must exceed max single-job routing cost |
| `age_weight` | `200` | Per day since sold |
| `age_cap_days` | `120` | Prevents one ancient job dominating |
| `horizon_days` | `10` | Solve jointly |
| `frozen_days` | `2` | Committed to customers; never re-solved |
| `heavy_jobs_per_crew_day` | `1` | MPU / TDR / Tap Box |

`max_travel_minutes` is deliberately gone. A hard travel cap was a proxy for "I can't
afford to evaluate this properly." The solver optimizes total drive time directly, which
is what you actually wanted; a hard per-leg cap only blocks pairings that might well be
correct.
