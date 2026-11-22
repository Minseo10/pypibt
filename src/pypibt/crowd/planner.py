"""Congestion forecasts and a PIBT route-ranking adapter."""

import numpy as np

from pypibt import PIBT


class Forecast:
    def __init__(self, graph, routes, horizon):
        self.graph, self.routes, self.horizon = graph, routes, horizon
        self.visits = np.zeros(len(graph.cells))
        self.flows = np.zeros(len(graph.edges))
        for route in routes.values():
            np.add.at(self.visits, route[1:], 1 / horizon)
            edges = [graph.edge_index[u, v] for u, v in zip(route, route[1:])]
            np.add.at(self.flows, edges, 1 / horizon)

    def costs(self, agent, method, weight=2.0, counterflow=2.0):
        g = self.graph
        if method == "distance" or weight == 0:
            return np.ones(len(g.edges))
        if method not in ("traffic", "geometry"):
            raise ValueError(f"Unknown routing method: {method}")
        visits, flows = self.visits.copy(), self.flows.copy()
        route = self.routes[agent]
        np.add.at(visits, route[1:], -1 / self.horizon)
        edges = [g.edge_index[u, v] for u, v in zip(route, route[1:])]
        np.add.at(flows, edges, -1 / self.horizon)
        risk = np.maximum(visits[g.dst], 0) + counterflow * np.maximum(
            flows[g.reverse], 0
        )
        if method == "geometry":
            risk /= g.width[g.dst]
        return 1 + weight * risk


def forecast_routes(graph, positions, goals, fields, costs, horizon):
    routes = {}
    for agent, start in positions.items():
        route = [start]
        for _ in range(horizon):
            u = route[-1]
            if u == goals[agent]:
                break
            v = min(
                graph.neighbors[u],
                key=lambda v: (
                    costs[agent][graph.edge_index[u, v]] + fields[agent][v],
                    v,
                ),
            )
            route.append(v)
        routes[agent] = route
    return routes


class StudyPIBT(PIBT):
    def __init__(self, graph, starts, goals, seed):
        super().__init__(
            graph.grid,
            [graph.cells[v] for v in starts],
            [graph.cells[v] for v in goals],
            seed,
        )
        self.graph = graph
        self.fields = {i: graph.distances(goal) for i, goal in enumerate(goals)}
        self.costs = {i: np.ones(len(graph.edges)) for i in range(len(starts))}

    def candidate_score(self, i, origin, target):
        agent = self.active_ids[i]
        u, v = self.graph.index[origin], self.graph.index[target]
        edge_cost = 1 if u == v else self.costs[agent][self.graph.edge_index[u, v]]
        return float(edge_cost + self.fields[agent][v])

    def active_step(self, positions, priorities):
        self.active_ids = sorted(positions)
        self.N = self.NIL = len(self.active_ids)
        self.occupied_now.fill(self.NIL)
        self.occupied_nxt.fill(self.NIL)
        current = [self.graph.cells[positions[i]] for i in self.active_ids]
        result = super().step(current, [priorities[i] for i in self.active_ids])
        return {i: self.graph.index[q] for i, q in zip(self.active_ids, result)}


def validate_step(graph, before, after):
    if before.keys() != after.keys():
        raise ValueError("Agent set changed during a step")
    if len(set(after.values())) != len(after):
        raise ValueError("Vertex collision")
    owner = {u: i for i, u in before.items()}
    for i, v in after.items():
        u = before[i]
        if v != u and v not in graph.neighbors[u]:
            raise ValueError("Invalid move")
        j = owner.get(v)
        if j is not None and j != i and after[j] == u:
            raise ValueError("Edge swap")
