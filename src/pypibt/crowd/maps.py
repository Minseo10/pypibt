"""Parameterized layouts and paired start/goal scenarios."""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_cdt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from pypibt.mapf_utils import get_neighbors


@dataclass
class Graph:
    grid: np.ndarray

    def __post_init__(self):
        self.cells = [tuple(map(int, p)) for p in np.argwhere(self.grid)]
        self.index = {p: i for i, p in enumerate(self.cells)}
        self.neighbors = [
            [self.index[q] for q in get_neighbors(self.grid, p)] for p in self.cells
        ]
        self.edges = [(u, v) for u, ns in enumerate(self.neighbors) for v in ns]
        self.edge_index = {e: i for i, e in enumerate(self.edges)}
        self.src, self.dst = np.array(self.edges, dtype=np.int32).T
        self.reverse = np.array([self.edge_index[v, u] for u, v in self.edges])
        clearance = distance_transform_cdt(np.pad(self.grid, 1), metric="taxicab")[
            1:-1, 1:-1
        ]
        self.width = np.array([np.clip(2 * clearance[p] - 1, 1, 5) for p in self.cells])
        self.matrix = csr_matrix(
            (np.ones(len(self.edges)), (self.src, self.dst)),
            shape=(len(self.cells), len(self.cells)),
        )
        if connected_components(self.matrix, directed=False, return_labels=False) != 1:
            raise ValueError("Map must be connected")

    def distances(self, goal, costs=None):
        matrix = (
            self.matrix
            if costs is None
            else csr_matrix((costs, (self.src, self.dst)), shape=self.matrix.shape)
        )
        return dijkstra(matrix.T.tocsr(), indices=goal, directed=True)

    def bridge_count(self):
        # Iterative DFS avoids recursion limits on large grids.
        discovered, low, parent = {0: 0}, {0: 0}, {0: -1}
        stack, count = [(0, iter(self.neighbors[0]))], 0
        while stack:
            u, children = stack[-1]
            v = next(children, None)
            if v is None:
                stack.pop()
                p = parent[u]
                if p >= 0:
                    count += low[u] > discovered[p]
                    low[p] = min(low[p], low[u])
            elif v not in discovered:
                parent[v] = u
                discovered[v] = low[v] = len(discovered)
                stack.append((v, iter(self.neighbors[v])))
            elif v != parent[u]:
                low[u] = min(low[u], discovered[v])
        return int(count)


def make_map(kind="funnel", size=32, width=2, length=12, barrier_size=5):
    if size < 24 or not 1 <= width <= size // 3 or not 2 <= length <= size - 16:
        raise ValueError("Use size >= 24, width in [1,size//3], length in [2,size-16]")
    if not 1 <= barrier_size <= size // 3:
        raise ValueError("Invalid barrier_size")
    grid = np.ones((size, size), dtype=bool)
    grid[[0, -1], :] = False
    grid[:, [0, -1]] = False
    mid = size // 2
    if kind == "funnel":
        for x in range(8, mid + 2):
            aperture = max(width, 2 * (mid + 1 - x) + width)
            lo = mid - aperture // 2
            hi = lo + aperture
            grid[4 : max(4, lo), x] = False
            grid[min(size - 4, hi) : size - 4, x] = False
    elif kind == "barrier":
        lo = mid - barrier_size // 2
        grid[lo : lo + barrier_size, lo : lo + barrier_size] = False
    elif kind == "walls":
        lo = mid - width // 2
        left = mid - length // 2
        grid[lo - 1, left : left + length] = False
        grid[lo + width, left : left + length] = False
    elif kind != "open":
        raise ValueError(f"Unknown map: {kind}")
    return grid


def scenario(graph, n, seed, traffic="opposing"):
    if traffic not in ("one-way", "opposing"):
        raise ValueError("traffic must be one-way or opposing")
    size = graph.grid.shape[1]
    left = [
        i for i, (y, x) in enumerate(graph.cells) if 4 <= y < size - 4 and 1 <= x <= 6
    ]
    right = [
        i
        for i, (y, x) in enumerate(graph.cells)
        if 4 <= y < size - 4 and size - 7 <= x < size - 1
    ]
    if not 1 <= n <= min(len(left), len(right)):
        raise ValueError("Too many robots for the endpoint regions")
    rng = np.random.default_rng(seed)
    ls, rs = rng.permutation(left)[:n], rng.permutation(right)[:n]
    lg, rg = rng.permutation(left)[:n], rng.permutation(right)[:n]
    split = n if traffic == "one-way" else (n + 1) // 2
    starts = np.concatenate([ls[:split], rs[split:]]).tolist()
    goals = np.concatenate([rg[:split], lg[split:]]).tolist()
    return starts, goals


def awareness(n, fraction, seed):
    if not 0 <= fraction <= 1:
        raise ValueError("Awareness fraction must be in [0,1]")
    mask = np.zeros(n, dtype=bool)
    order = np.random.default_rng(np.random.SeedSequence([seed, 1])).permutation(n)
    mask[order[: int(n * fraction)]] = True
    return mask
