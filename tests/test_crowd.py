import gzip
import json

import numpy as np
import pytest

from pypibt import PIBT
from pypibt.crowd.maps import Graph, awareness, make_map, scenario
from pypibt.crowd.planner import Forecast, StudyPIBT, validate_step
from pypibt.crowd.simulation import RunConfig, run


def test_maps_keep_endpoints_paired_and_awareness_nested():
    reference = None
    for kind in ("open", "funnel", "barrier", "walls"):
        graph = Graph(make_map(kind))
        starts, goals = scenario(graph, 100, 7)
        coords = ([graph.cells[v] for v in starts], [graph.cells[v] for v in goals])
        if reference is None:
            reference = coords
        assert coords == reference
        assert len(set(starts)) == len(set(goals)) == 100
        assert all(
            np.isfinite(graph.distances(goal)[start])
            for start, goal in zip(starts, goals)
        )
        assert graph.bridge_count() == 0
    assert np.all(awareness(100, 0.25, 7) <= awareness(100, 0.5, 7))
    assert awareness(100, 0.25, 7).sum() == 25
    assert Graph(np.ones((1, 3), dtype=bool)).bridge_count() == 2


def test_forecast_excludes_self_and_counts_reverse_flow():
    graph = Graph(np.ones((3, 4), dtype=bool))
    a, b = graph.index[1, 1], graph.index[1, 2]
    edge = graph.edge_index[a, b]
    single = Forecast(graph, {0: [a, b]}, 2)
    assert np.allclose(single.costs(0, "geometry"), 1)
    forecast = Forecast(graph, {0: [a, b], 1: [b, a], 2: [a, b]}, 2)
    assert forecast.costs(0, "traffic", weight=2, counterflow=2)[edge] == 4
    assert forecast.costs(0, "geometry", weight=2, counterflow=2)[edge] == 2
    assert forecast.costs(0, "traffic", weight=2, counterflow=0)[edge] == 2


def test_congestion_can_change_route_and_reverse_dijkstra_direction():
    graph = Graph(np.ones((3, 5), dtype=bool))
    start, middle, goal = [graph.index[1, x] for x in (0, 1, 4)]
    forecast = Forecast(graph, {0: [start], 1: [start, middle]}, 1)
    costs = forecast.costs(0, "traffic", weight=20, counterflow=0)
    distances = graph.distances(goal, costs)
    best = min(
        graph.neighbors[start],
        key=lambda v: costs[graph.edge_index[start, v]] + distances[v],
    )
    assert best != middle
    assert distances[start] == 6
    assert graph.distances(goal)[start] == 4


def test_zero_weight_matches_upstream_and_arrived_agents_leave():
    graph = Graph(np.ones((3, 5), dtype=bool))
    starts = [graph.index[1, 0], graph.index[1, 4]]
    goals = starts[::-1]
    study = StudyPIBT(graph, starts, goals, 4)
    original = PIBT(
        graph.grid, [graph.cells[v] for v in starts], [graph.cells[v] for v in goals], 4
    )
    priorities = [0.0, 0.1]
    result = study.active_step(dict(enumerate(starts)), priorities)
    expected = original.step([graph.cells[v] for v in starts], priorities)
    assert [graph.cells[result[i]] for i in range(2)] == expected
    one = study.active_step({1: result[1]}, priorities)
    assert set(one) == {1}
    validate_step(graph, {1: result[1]}, one)
    baseline = run(RunConfig(method="distance", horizon=60, predictions=False))
    zero = run(RunConfig(method="geometry", weight=0, horizon=60))
    assert baseline["trajectory"] == zero["trajectory"]
    assert baseline["robots"] == zero["robots"]
    observed = run(RunConfig(method="distance", horizon=60, predictions=True))
    assert observed["trajectory"] == baseline["trajectory"]
    assert observed["predictions"]
    assert all(
        0 <= p["observed_wait"] <= p["window_steps"] <= 10
        for p in observed["predictions"]
    )


@pytest.mark.parametrize("method", ["distance", "traffic", "geometry"])
def test_runs_are_safe_and_timeout_metrics_include_every_robot(method):
    result = run(RunConfig(method=method, n=50, horizon=70, seed=5))
    frames = result["trajectory"]["frames"]
    assert all(r["waits"] + r["moves"] == r["capped_arrival"] for r in result["robots"])
    for robot in result["robots"]:
        arrival = robot["arrival"]
        if arrival is not None:
            assert all(frame[robot["agent"]] is None for frame in frames[arrival + 1 :])
    timeout = run(RunConfig(method=method, horizon=1))
    assert timeout["summary"]["completion"] == 0
    assert timeout["summary"]["capped_arrival"] == 1
    assert timeout["summary"]["detour_ratio"] is None
    assert timeout["predictions"] == []
    assert np.isfinite(result["summary"]["planning_ms"])


def test_validator_rejects_collision_swap_and_teleport():
    graph = Graph(np.ones((1, 4), dtype=bool))
    for after in ({0: 1, 1: 1}, {0: 1, 1: 0}, {0: 3, 1: 1}):
        with pytest.raises(ValueError):
            validate_step(graph, {0: 0, 1: 1}, after)


def test_output_resume_analysis_and_pymunk_video(tmp_path):
    import imageio.v2 as imageio

    from pypibt.crowd.analysis import analyze
    from pypibt.crowd.cli import configurations, run_saved, write_csv
    from pypibt.crowd.replay import replay_video

    configs = list(
        configurations(
            {
                "defaults": {"n": 4, "horizon": 20},
                "sweep": {"fraction": [0, 0.5], "method": ["distance", "geometry"]},
            }
        )
    )
    assert len(configs) == 2
    numeric_cases = list(configurations({"cases": [{"weight": 2}, {"weight": 2.0}]}))
    assert len(numeric_cases) == 1
    rows = [run_saved(c, tmp_path) for c in configs]
    assert run_saved(configs[0], tmp_path) == rows[0]
    write_csv(tmp_path / "summary.csv", rows)
    analyze(tmp_path)
    assert (tmp_path / "aggregate.csv").exists()
    assert list((tmp_path / "figures").glob("*.png"))
    trajectory = tmp_path / "runs" / rows[1]["run_id"] / "trajectory.json.gz"
    with gzip.open(trajectory, "rt") as stream:
        assert json.load(stream)["frames"]
    video = tmp_path / "replay.mp4"
    assert replay_video(trajectory, video, substeps=1, max_steps=2) == 2
    reader = imageio.get_reader(video)
    assert reader.get_data(0).shape[2] == 3
    reader.close()
