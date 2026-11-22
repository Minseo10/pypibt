"""Reproducible run, sweep, plot, and replay commands."""

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import itertools
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from .simulation import RunConfig, run


def write_csv(path, rows):
    with Path(path).open("w", newline="") as stream:
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def source_hash():
    root = Path(__file__).parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def provenance():
    def git(*args):
        try:
            return subprocess.check_output(
                ["git", *args], stderr=subprocess.DEVNULL, text=True
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    packages = {
        p: importlib.metadata.version(p)
        for p in ["numpy", "scipy", "matplotlib", "pymunk", "imageio", "imageio-ffmpeg"]
    }
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "source_sha256": source_hash(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run_saved(config, output):
    data = asdict(config)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]
    run_id = f"{config.map}-{config.method}-n{config.n}-p{config.fraction:g}-s{config.seed}-{digest}"
    folder = Path(output) / "runs" / run_id
    if (folder / "summary.json").exists():
        meta = json.loads((folder / "provenance.json").read_text())
        if meta["source_sha256"] != source_hash():
            raise ValueError(f"Source changed; use a new output folder: {folder}")
        return {
            "run_id": run_id,
            **data,
            **json.loads((folder / "summary.json").read_text()),
        }
    result = run(config)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text(json.dumps(data, indent=2) + "\n")
    (folder / "provenance.json").write_text(json.dumps(provenance(), indent=2) + "\n")
    write_csv(folder / "robots.csv", result["robots"])
    write_csv(folder / "predictions.csv", result["predictions"])
    with gzip.open(folder / "trajectory.json.gz", "wt") as stream:
        json.dump(result["trajectory"], stream)
    pending = folder / "summary.json.tmp"
    pending.write_text(json.dumps(result["summary"], indent=2) + "\n")
    pending.replace(folder / "summary.json")
    return {"run_id": run_id, **data, **result["summary"]}


def configurations(spec):
    seen = set()
    sweep = spec.get("sweep", {})
    keys = list(sweep)
    for case in spec.get("cases", [{}]):
        for values in itertools.product(*(sweep[k] for k in keys)):
            c = RunConfig(
                **{**spec.get("defaults", {}), **case, **dict(zip(keys, values))}
            )
            if c.method == "distance" or c.fraction == 0:
                c = replace(c, method="distance", fraction=0.0)
            key = json.dumps(asdict(c), sort_keys=True)
            if key not in seen:
                seen.add(key)
                yield c


def sweep_file(config_path, output):
    spec = json.loads(Path(config_path).read_text())
    configs = list(configurations(spec))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    specification = output / "specification.json"
    if specification.exists() and json.loads(specification.read_text()) != spec:
        raise ValueError("Output folder belongs to a different sweep")
    specification.write_text(json.dumps(spec, indent=2) + "\n")
    rows = []
    for i, c in enumerate(configs, 1):
        row = run_saved(c, output)
        rows.append(row)
        write_csv(output / "summary.csv", rows)
        print(
            f"{i}/{len(configs)} {row['run_id']}: completion={row['completion']:.3f}, arrival={row['capped_arrival']:.2f}",
            flush=True,
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("run")
    single.add_argument("--config", required=True, type=Path)
    single.add_argument("--output", required=True, type=Path)
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--config", required=True, type=Path)
    sweep.add_argument("--output", required=True, type=Path)
    plot = sub.add_parser("plot")
    plot.add_argument("directory", type=Path)
    replay = sub.add_parser("replay")
    replay.add_argument("trajectory", type=Path)
    replay.add_argument("--output", required=True, type=Path)
    replay.add_argument("--fps", type=int, default=12)
    replay.add_argument("--substeps", type=int, default=3)
    replay.add_argument("--max-steps", type=int, default=150)
    args = parser.parse_args()
    if args.command == "run":
        config = RunConfig(**json.loads(args.config.read_text()))
        row = run_saved(config, args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        write_csv(args.output / "summary.csv", [row])
        print(json.dumps(row, indent=2))
    elif args.command == "sweep":
        sweep_file(args.config, args.output)
    elif args.command == "plot":
        from .analysis import analyze

        analyze(args.directory)
    else:
        from .replay import replay_video

        replay_video(
            args.trajectory, args.output, args.fps, args.substeps, args.max_steps
        )


if __name__ == "__main__":
    main()
