# Partial congestion awareness study

This fork asks how awareness fraction, population density, and obstacle geometry affect collective arrival performance. It compares distance-only, traffic-aware, and traffic-and-geometry-aware route preferences inside the same PIBT coordinator.

## Start and implementation sequence

```bash
uv sync --locked --extra study --no-dev
uv run --locked --extra study --no-dev python app.py \
  -m assets/random-32-32-10.map \
  -i assets/random-32-32-10-random-1.scen -N 200
bash scripts/ci_check.sh
```

1. **Baseline:** `StudyPIBT` delegates collision resolution and priority inheritance to upstream PIBT. The only upstream extension is `candidate_score`. Arrived robots are removed before the next step; this intentionally differs from upstream's persistent-at-goal convention.
2. **Maps:** `make_map` generates open, funnel, isolated barrier, and flanking-wall layouts. `scenario` selects disjoint start/goal positions from fixed left/right regions. Seeds pair the same endpoint coordinates across maps and methods. Awareness is a nested seeded subset of robot IDs, independent of planning randomness.
3. **Mixed preferences:** `Forecast` builds vertex and reverse-edge traffic estimates. Reverse Dijkstra computes per-agent cost-to-go fields; PIBT ranks moves using those fields.
4. **Validation and experiments:** safety checks run every step. Passive forecasts in distance-only runs measure prediction quality without changing motion. The CLI records per-robot metrics, trajectories, run settings, source hash, revision, environment, and timing.
5. **Schedule:** baseline/maps → method/validation → experiments/report. Pymunk is a replay layer, not the source of the numerical results.

## Definitions and assumptions

- Robots occupy one grid cell, move to a 4-neighbor or wait, and disappear at first arrival. Distinct endpoint positions are sampled before routing. No vertex collisions or edge swaps are allowed.
- Every `replan` steps (default 5), project the next `forecast` steps (default 10) along each active robot's current preferred route. Initialization uses shortest-path distance. Forecasts ignore collisions and stop at goals. All agent updates use the same frozen snapshot.
- For move `u → v`, `F` is other agents' forecast visits to `v` divided by the forecast horizon; `B` is their reverse traversals `v → u` divided by that horizon. A robot's own predicted visits and traversals are subtracted.
- Width proxy: `w(v) = clip(2*d_obs(v)-1, 1, 5)`, with Manhattan distance to obstacles or the blocked border. This is local clearance, not measured bottleneck capacity.

| Rule | Edge cost |
| --- | --- |
| Distance-only | `1` |
| Traffic-aware | `1 + weight*(F + counterflow*B)` |
| Geometry-aware | `1 + weight*(F + counterflow*B)/w(v)` |

Defaults are `weight=2`, `counterflow=2`. Unaware robots always use cost 1. Waiting costs 1; candidate moves are ranked by edge cost plus cost-to-go. Greedy forecasts use deterministic vertex-ID tie breaks, never consume PIBT's seeded tie-breaking stream, and positive edge costs yield descending cost-to-go routes.

The score is **uncalibrated congestion risk**, not seconds of delay. Funnel queues, splitting around a point barrier, and sustained opposing traffic between walls may violate its assumptions. No completeness guarantee is claimed for the modified policy. `bridges` in the summary flags bridge-edge counts; a zero count alone is not proof that modified guidance will finish.

## Commands and configurations

```bash
# End-to-end smoke run (three methods, seed 0, 20 robots).
uv run --locked --extra study --no-dev crowd-study sweep \
  --config configs/smoke.json --output results/smoke
uv run --locked --extra study --no-dev crowd-study plot results/smoke

# Main experiment: 4 maps, N=20/50/100, p=0/.25/.5/.75/1, seeds 0..19.
uv run --locked --extra study --no-dev crowd-study sweep \
  --config configs/full.json --output results/main
uv run --locked --extra study --no-dev crowd-study plot results/main

# Geometry controls and parameter ablations.
uv run --locked --extra study --no-dev crowd-study sweep \
  --config configs/geometry.json --output results/geometry
uv run --locked --extra study --no-dev crowd-study plot results/geometry
uv run --locked --extra study --no-dev crowd-study sweep \
  --config configs/ablations.json --output results/ablations
uv run --locked --extra study --no-dev crowd-study plot results/ablations
```

- `configs/pilot.json` uses three seeds and the same main conditions (324 unique runs); the full sweep uses 2,160 unique runs. Identical `p=0` baselines are computed once, not once per routing model.
- Geometry controls fix `N=50`, `p=.5`, vary funnel width, barrier size, or corridor width/length, and include both traffic directions. Three seeds make this a diagnostic study, not a definitive parameter comparison.
- Ablations vary the forecast horizon, congestion weight, and counterflow term on funnel/wall maps. They are exploratory; no automatic parameter selection or test-set tuning occurs.
- To create a custom sweep, copy a configuration and change `defaults`, `cases`, or `sweep`. Values in `sweep` override `cases`, which override `defaults`. `crowd-study run --config single.json --output results/single` accepts one `RunConfig` object instead.
- A sweep resumes completed runs only with identical source hashes. Use a new output directory after code changes or for a different specification. Do not edit sources during a run. Execution is serial so wall-clock planning measurements are not distorted by parallel runs in the same sweep.
- A larger map can accommodate more endpoint robots; invalid sizes or populations raise errors instead of silently changing scenarios. Geometry changes preserve endpoint regions, but do not equalize route lengths, free area, or bypass costs. Consult the logged `free_cells`, `density`, `solo_distance`, and geometry parameters before attributing effects solely to obstacle type.

## Metrics and figures

- **Completion:** arrivals / total robots by `horizon` (default 500 steps).
- **Capped arrival:** mean arrival step, assigning the horizon to unfinished robots. Always report completion alongside it.
- **Waiting and moves:** counts before arrival or timeout. Their sum equals capped arrival for every robot.
- **Detour:** moves / solo shortest-path distance, only for arrivals. Missing groups or no arrivals have no detour value. Interpret together with group completion rates.
- **Group burden:** aware/unaware completion, capped arrival, waits, and detours; `wait_gap` is aware minus unaware mean waiting. This is descriptive, not a causal comparison of individual robot types.
- **Timing:** per-step wall time includes active forecasting, weighted Dijkstra, and PIBT. Setup and passive diagnostic computation are logged separately. Timings depend on hardware and system load.
- `summary.csv` retains every run; `aggregate.csv` gives mean metrics and seed-bootstrap intervals; `paired_differences.csv` gives within-seed changes relative to the corresponding distance-only baseline.
- Performance panels plot completion, arrival, waiting, runtime, and group waits against awareness fraction. Burden panels show group detour and completion. Method curves share seeds; their bands are marginal intervals. Use the paired-difference table to assess changes.
- Prediction scatterplots compare summed forecast penalties with actual waits over the next forecast window, ending at arrival. Samples are taken in non-overlapping windows only during distance-only routing. No partial horizon-end windows are included. Correlations and bootstrap intervals are stratified by geometry settings, population, and model; resampling uses whole simulation seeds, not individual robot windows.
- Prediction scores are not calibrated travel-time estimates. Spearman correlation tests ordering, not absolute error or causality; constant-valued data yield an undefined correlation. A separate held-out calibration would be needed before reporting delay MAE.

## Pymunk replay and video

Choose a `trajectory.json.gz` under a completed run:

```bash
uv run --locked --extra study --no-dev crowd-study replay \
  results/main/runs/RUN_ID/trajectory.json.gz \
  --output results/main/videos/replay.mp4 --fps 12 --substeps 3 --max-steps 150
```

The replay uses Pymunk static obstacle bodies and kinematic robot bodies, rendering headlessly with Pillow. Orange robots are aware and blue robots are unaware. A PNG thumbnail is saved beside the H.264 MP4. The bundled FFmpeg executable is supplied by `imageio-ffmpeg`.

This reproduces grid trajectories through interpolation; physics does not alter paths. It is not a validation of continuous collision avoidance, acceleration limits, or real robot dynamics. A time-limited video can end before a run completes; use the CSV metrics for numerical results.

## Focused tests and CI

`bash scripts/ci_check.sh` uses the lockfile, compiles the new modules, and runs upstream tests plus checks for:

- paired endpoints, connectivity, and nested awareness assignments;
- exact self-exclusion, reverse-flow penalties, width correction, and detour choice;
- upstream-equivalent ranking at zero weight and passive logging that cannot affect motion;
- active-agent removal, collision rejection, and timeout-aware metrics;
- resumable outputs, plot generation, and a two-frame Pymunk video that is decoded again.

GitHub Actions runs the same script on Python 3.11 and 3.12 for pull requests and pushes to `main`. Experiment data, figures, videos, limitations, and the commands used belong in a separate results PR.

## Sources

- [PIBT and its original graph conditions](https://kei18.github.io/pibt2/)
- [Traffic Flow Optimisation for Lifelong Multi-Agent Path Finding](https://arxiv.org/abs/2308.11234)
- [Guided-PIBT reference implementation](https://github.com/nobodyczcz/Guided-PIBT)
