"""Seed-level summaries, uncertainty, and passive prediction plots."""

import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr

from .cli import write_csv


def load_csv(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream))


def bootstrap(values, seed=2026):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return np.nan, np.nan, np.nan
    mean = float(values.mean())
    if len(values) < 2:
        return mean, mean, mean
    rng = np.random.default_rng(seed)
    means = rng.choice(values, (2000, len(values)), replace=True).mean(axis=1)
    return mean, *np.percentile(means, [2.5, 97.5]).tolist()


def analyze(directory):
    directory = Path(directory)
    rows = load_csv(directory / "summary.csv")
    if not rows:
        raise ValueError("No runs to analyze")
    # Separate geometry/settings slices instead of pooling incompatible runs.
    facets = [
        "map",
        "size",
        "width",
        "length",
        "barrier_size",
        "traffic",
        "horizon",
        "forecast",
        "replan",
        "weight",
        "counterflow",
    ]
    slices = defaultdict(list)
    for row in rows:
        slices[tuple(row[k] for k in facets)].append(row)
    summaries, paired = [], []
    figures = directory / "figures"
    figures.mkdir(exist_ok=True)
    for index, (key, subset) in enumerate(sorted(slices.items())):
        label = dict(zip(facets, key))
        tag = f"{index:02d}-{label['map']}"
        title = f"{label['map']}: {label['traffic']}, w={label['width']}, L={label['length']}, barrier={label['barrier_size']}, K={label['forecast']}, weight={label['weight']}, beta={label['counterflow']}"
        fig, axes = plt.subplots(2, 3, figsize=(13, 7), constrained_layout=True)
        metrics = [
            "completion",
            "capped_arrival",
            "waits",
            "planning_ms",
            "aware_waits",
            "unaware_waits",
        ]
        metric_titles = [
            "Completion fraction",
            "Capped arrival (steps)",
            "Waits (steps)",
            "Planning time (ms/step)",
            "Aware waits (steps)",
            "Unaware waits (steps)",
        ]
        for n_index, n in enumerate(sorted({int(r["n"]) for r in subset})):
            baseline = [
                r for r in subset if int(r["n"]) == n and r["method"] == "distance"
            ]
            methods = sorted({r["method"] for r in subset if r["method"] != "distance"})
            for method in methods or ["distance"]:
                chosen = [
                    r for r in subset if int(r["n"]) == n and r["method"] == method
                ]
                if method != "distance":
                    chosen += baseline
                groups = defaultdict(list)
                for row in chosen:
                    groups[float(row["fraction"])].append(row)
                for ax, metric, metric_title in zip(axes.flat, metrics, metric_titles):
                    xs, ys, lows, highs = [], [], [], []
                    for fraction, group in sorted(groups.items()):
                        values = [float(r[metric]) for r in group if r[metric] != ""]
                        if not values:
                            continue
                        mean, low, high = bootstrap(values)
                        xs.append(fraction)
                        ys.append(mean)
                        lows.append(low)
                        highs.append(high)
                    color = plt.get_cmap("tab10")(n_index)
                    style = "-" if method in ("geometry", "distance") else "--"
                    ax.plot(
                        xs, ys, style, marker="o", label=f"{method}, N={n}", color=color
                    )
                    ax.fill_between(xs, lows, highs, color=color, alpha=0.12)
                    ax.set(xlabel="Aware fraction", title=metric_title)
                    ax.grid(alpha=0.2)
            for method in sorted({r["method"] for r in subset}):
                for fraction in sorted(
                    {float(r["fraction"]) for r in subset if r["method"] == method}
                ):
                    group = [
                        r
                        for r in subset
                        if r["method"] == method
                        and int(r["n"]) == n
                        and float(r["fraction"]) == fraction
                    ]
                    if not group:
                        continue
                    record = {
                        **label,
                        "n": n,
                        "method": method,
                        "fraction": fraction,
                        "seeds": len(group),
                    }
                    for metric in [
                        "completion",
                        "capped_arrival",
                        "waits",
                        "wait_gap",
                        "detour_ratio",
                        "planning_ms",
                        "aware_completion",
                        "unaware_completion",
                        "aware_detour_ratio",
                        "unaware_detour_ratio",
                    ]:
                        vals = [float(r[metric]) for r in group if r[metric] != ""]
                        mean, lo, hi = bootstrap(vals)
                        record.update(
                            {metric: mean, metric + "_low": lo, metric + "_high": hi}
                        )
                    summaries.append(record)
                    base_by_seed = {r["seed"]: r for r in baseline}
                    for metric in ("capped_arrival", "completion"):
                        deltas = [
                            float(r[metric]) - float(base_by_seed[r["seed"]][metric])
                            for r in group
                            if r["seed"] in base_by_seed
                        ]
                        mean, lo, hi = bootstrap(deltas)
                        paired.append(
                            {
                                **label,
                                "n": n,
                                "method": method,
                                "fraction": fraction,
                                "metric": metric,
                                "paired_seeds": len(deltas),
                                "difference": mean,
                                "low": lo,
                                "high": hi,
                            }
                        )
        axes[0, 0].legend(fontsize=7)
        fig.suptitle(
            title
            + "\nBands: seed-bootstrap 95% intervals; individual method curves, not paired differences"
        )
        fig.savefig(figures / f"{tag}-performance.png", dpi=140)
        plt.close(fig)
        burden_plot(subset, figures / f"{tag}-burden.png", title)
    write_csv(directory / "aggregate.csv", summaries)
    write_csv(directory / "paired_differences.csv", paired)
    prediction_analysis(directory, rows, figures)
    (directory / "analysis_metadata.json").write_text(
        json.dumps(
            {
                "bootstrap_resamples": 2000,
                "bootstrap_seed": 2026,
                "interval_unit": "whole simulation seed",
                "timing": "planning wall time, excludes setup and passive diagnostics",
                "detour_population": "arrived robots only; consult group completion rates",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote aggregate tables and figures to {directory}")


def prediction_analysis(directory, runs, figures):
    groups = defaultdict(list)
    for run in runs:
        if run["method"] != "distance":
            continue
        for row in load_csv(directory / "runs" / run["run_id"] / "predictions.csv"):
            keys = [
                "map",
                "size",
                "width",
                "length",
                "barrier_size",
                "traffic",
                "n",
                "horizon",
                "forecast",
                "replan",
                "weight",
                "counterflow",
            ]
            key = tuple(run[k] for k in keys) + (row["model"],)
            groups[key].append({**row, "seed": run["seed"], "run_id": run["run_id"]})
    stats = []
    for index, (key, rows) in enumerate(sorted(groups.items())):
        label = dict(zip(keys + ["model"], key))
        geometry, model = label["map"], label["model"]
        x = np.array([float(r["risk"]) for r in rows])
        y = np.array([float(r["observed_wait"]) for r in rows])

        def correlation(a, b):
            return (
                float(spearmanr(a, b).statistic)
                if np.ptp(a) > 0 and np.ptp(b) > 0
                else np.nan
            )

        rho = correlation(x, y)
        seeds = sorted({r["seed"] for r in rows})
        rng = np.random.default_rng(2026)
        by_seed = {
            s: np.array([i for i, r in enumerate(rows) if r["seed"] == s])
            for s in seeds
        }
        draws = []
        if len(seeds) > 1:
            for _ in range(500):
                ids = np.concatenate(
                    [by_seed[s] for s in rng.choice(seeds, len(seeds), replace=True)]
                )
                draws.append(correlation(x[ids], y[ids]))
        finite = [v for v in draws if np.isfinite(v)]
        lo, hi = np.percentile(finite, [2.5, 97.5]) if finite else (np.nan, np.nan)
        stats.append(
            {
                **label,
                "windows": len(rows),
                "seeds": len(seeds),
                "spearman": rho,
                "low": float(lo),
                "high": float(hi),
                "zero_wait_fraction": float(np.mean(y == 0)),
            }
        )
        fig, ax = plt.subplots(figsize=(6, 4), constrained_layout=True)
        ax.scatter(x, y, alpha=0.12, s=9)
        if np.ptp(x) > 0:
            bins = np.unique(np.quantile(x, np.linspace(0, 1, 8)))
            bx, by = [], []
            for j, (left, right) in enumerate(zip(bins, bins[1:])):
                mask = (x >= left) & (
                    (x <= right) if j == len(bins) - 2 else (x < right)
                )
                if mask.any():
                    bx.append(x[mask].mean())
                    by.append(y[mask].mean())
            ax.plot(bx, by, "o-", color="#d56b15", label="Binned mean")
            ax.legend()
        ax.set(
            xlabel="Uncalibrated forecast risk",
            ylabel="Observed waits in forecast window",
            title=f"{geometry} / {model}: Spearman={rho:.2f}\nDistance-only routing; N={label['n']}; {len(seeds)} seeds",
        )
        fig.savefig(figures / f"prediction-{index:02d}-{geometry}-{model}.png", dpi=140)
        plt.close(fig)
    write_csv(directory / "prediction_summary.csv", stats)


def burden_plot(rows, output, title):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for method in sorted({r["method"] for r in rows} - {"distance"}):
        for ni, n in enumerate(sorted({int(r["n"]) for r in rows})):
            subset = [r for r in rows if r["method"] == method and int(r["n"]) == n]
            for flag in ("aware", "unaware"):
                for ax, metric in zip(axes, ("detour_ratio", "completion")):
                    x, y, low, high = [], [], [], []
                    for fraction in sorted({float(r["fraction"]) for r in subset}):
                        values = [
                            float(r[f"{flag}_{metric}"])
                            for r in subset
                            if float(r["fraction"]) == fraction
                            and r[f"{flag}_{metric}"] != ""
                        ]
                        if values:
                            mean, lo, hi = bootstrap(values)
                            x.append(fraction)
                            y.append(mean)
                            low.append(lo)
                            high.append(hi)
                    color = plt.get_cmap("tab10")(ni)
                    ax.plot(
                        x,
                        y,
                        "-" if flag == "aware" else "--",
                        marker="o" if method == "geometry" else "x",
                        color=color,
                        label=f"{method}, {flag}, N={n}",
                    )
                    ax.fill_between(x, low, high, color=color, alpha=0.07)
    axes[0].set(
        ylabel="Moves / solo shortest distance (arrivals only)", xlabel="Aware fraction"
    )
    axes[1].set(ylabel="Group completion fraction", xlabel="Aware fraction")
    axes[1].legend(fontsize=6, ncol=2)
    fig.suptitle(title + "\nGroups with no members are omitted")
    fig.savefig(output, dpi=140)
    plt.close(fig)
