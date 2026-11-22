"""One-shot runs, censored metrics, and passive prediction diagnostics."""

import time
from dataclasses import asdict, dataclass

import numpy as np

from .maps import Graph, awareness, make_map, scenario
from .planner import Forecast, StudyPIBT, forecast_routes, validate_step


@dataclass(frozen=True)
class RunConfig:
    map: str = "funnel"
    size: int = 32
    width: int = 2
    length: int = 12
    barrier_size: int = 5
    traffic: str = "opposing"
    n: int = 20
    seed: int = 0
    fraction: float = 0.5
    method: str = "geometry"
    horizon: int = 500
    forecast: int = 10
    replan: int = 5
    weight: float = 2.0
    counterflow: float = 2.0
    predictions: bool = True

    def __post_init__(self):
        if self.method not in ("distance", "traffic", "geometry"):
            raise ValueError("Unknown routing method")
        if min(self.n, self.horizon, self.forecast, self.replan) < 1:
            raise ValueError("Counts and horizons must be positive")
        if self.seed < 0 or not 0 <= self.fraction <= 1:
            raise ValueError("Invalid seed or awareness fraction")
        if (
            not np.isfinite([self.weight, self.counterflow]).all()
            or min(self.weight, self.counterflow) < 0
        ):
            raise ValueError("Congestion weights must be finite and nonnegative")


def run(config):
    c = config
    graph = Graph(make_map(c.map, c.size, c.width, c.length, c.barrier_size))
    starts, goals = scenario(graph, c.n, c.seed, c.traffic)
    aware = awareness(c.n, c.fraction, c.seed)
    begun = time.perf_counter()
    planner = StudyPIBT(graph, starts, goals, c.seed)
    setup_ms = (time.perf_counter() - begun) * 1000
    solo = np.array([planner.fields[i][v] for i, v in enumerate(starts)])
    positions = dict(enumerate(starts))
    priorities = solo / graph.grid.size
    arrivals = np.full(c.n, -1, dtype=int)
    waits, moves = np.zeros(c.n, dtype=int), np.zeros(c.n, dtype=int)
    frames = [[list(graph.cells[v]) for v in starts]]
    wait_history = [waits.copy()]
    timings, probes = [], []
    diagnostic_ms = 0.0
    for t in range(c.horizon):
        if not positions:
            break
        if (
            c.predictions
            and c.method == "distance"
            and t % c.forecast == 0
            and t + c.forecast <= c.horizon
        ):
            tick = time.perf_counter()
            routes = forecast_routes(
                graph, positions, goals, planner.fields, planner.costs, c.forecast
            )
            forecast = Forecast(graph, routes, c.forecast)
            for i, route in routes.items():
                edges = [graph.edge_index[u, v] for u, v in zip(route, route[1:])]
                for model in ("traffic", "geometry"):
                    score = float(
                        (
                            forecast.costs(i, model, c.weight, c.counterflow)[edges] - 1
                        ).sum()
                    )
                    probes.append(
                        {"agent": i, "time": t, "model": model, "risk": score}
                    )
            diagnostic_ms += (time.perf_counter() - tick) * 1000
        tick = time.perf_counter()
        if (
            c.method != "distance"
            and c.weight > 0
            and aware.any()
            and t % c.replan == 0
        ):
            routes = forecast_routes(
                graph, positions, goals, planner.fields, planner.costs, c.forecast
            )
            forecast = Forecast(graph, routes, c.forecast)
            for i in positions:
                if aware[i]:
                    planner.costs[i] = forecast.costs(
                        i, c.method, c.weight, c.counterflow
                    )
                    planner.fields[i] = graph.distances(goals[i], planner.costs[i])
        next_positions = planner.active_step(positions, priorities)
        timings.append((time.perf_counter() - tick) * 1000)
        validate_step(graph, positions, next_positions)
        frame = [None] * c.n
        for i, v in next_positions.items():
            waits[i] += v == positions[i]
            moves[i] += v != positions[i]
            frame[i] = list(graph.cells[v])
            if v == goals[i]:
                arrivals[i] = t + 1
            else:
                priorities[i] += 1
        frames.append(frame)
        positions = {i: v for i, v in next_positions.items() if arrivals[i] < 0}
        wait_history.append(waits.copy())
    for row in probes:
        i, t = row["agent"], row["time"]
        end = min(t + c.forecast, int(arrivals[i]) if arrivals[i] >= 0 else c.horizon)
        row["observed_wait"] = int(wait_history[end][i] - wait_history[t][i])
        row["window_steps"] = end - t
    robots = [
        {
            "agent": i,
            "aware": bool(aware[i]),
            "arrival": int(arrivals[i]) if arrivals[i] >= 0 else None,
            "completed": bool(arrivals[i] >= 0),
            "capped_arrival": int(arrivals[i]) if arrivals[i] >= 0 else c.horizon,
            "waits": int(waits[i]),
            "moves": int(moves[i]),
            "solo_distance": float(solo[i]),
            "detour_ratio": float(moves[i] / solo[i])
            if arrivals[i] >= 0 and solo[i] > 0
            else None,
        }
        for i in range(c.n)
    ]

    def mean(rows, key):
        values = [r[key] for r in rows if r[key] is not None]
        return float(np.mean(values)) if values else None

    summary = {
        "completion": mean(robots, "completed"),
        "capped_arrival": mean(robots, "capped_arrival"),
        "waits": mean(robots, "waits"),
        "moves": mean(robots, "moves"),
        "detour_ratio": mean(robots, "detour_ratio"),
        "planning_ms": float(np.mean(timings)),
        "planning_p95_ms": float(np.percentile(timings, 95)),
        "setup_ms": setup_ms,
        "diagnostic_ms": diagnostic_ms,
        "steps": len(timings),
        "free_cells": len(graph.cells),
        "density": c.n / len(graph.cells),
        "bridges": graph.bridge_count(),
        "solo_distance": float(solo.mean()),
    }
    for label, flag in [("aware", True), ("unaware", False)]:
        group = [r for r in robots if r["aware"] == flag]
        for metric in ("completion", "capped_arrival", "waits", "detour_ratio"):
            summary[f"{label}_{metric}"] = mean(
                group, "completed" if metric == "completion" else metric
            )
    summary["wait_gap"] = (
        summary["aware_waits"] - summary["unaware_waits"]
        if 0 < aware.sum() < c.n
        else None
    )
    return {
        "config": asdict(c),
        "summary": summary,
        "robots": robots,
        "predictions": probes,
        "trajectory": {
            "grid": graph.grid.astype(int).tolist(),
            "starts": frames[0],
            "goals": [list(graph.cells[v]) for v in goals],
            "aware": aware.tolist(),
            "frames": frames,
            "arrivals": [r["arrival"] for r in robots],
        },
    }
