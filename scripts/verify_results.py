"""Check the published experiment artifacts against their manifests."""

import csv
import hashlib
import io
import json
from pathlib import Path
import tarfile


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Artifact hash mismatch: {directory / name}")
    with (directory / "summary.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    ids = [r["run_id"] for r in rows]
    if len(set(ids)) != len(ids) or len(ids) != manifest["run_count"]:
        raise ValueError(f"Duplicate or missing runs: {directory}")
    by_id = {r["run_id"]: r for r in rows}
    seen = set()
    with tarfile.open(directory / "raw_runs.tar.gz", "r|gz") as archive:
        for member in archive:
            parts = Path(member.name).parts
            if len(parts) != 3 or parts[0] != "runs":
                continue
            _, run_id, filename = parts
            if filename not in ("summary.json", "provenance.json", "robots.csv"):
                continue
            if run_id not in by_id:
                raise ValueError(f"Unlisted run: {run_id}")
            row = by_id[run_id]
            seen.add((run_id, filename))
            stream = archive.extractfile(member)
            if filename == "summary.json":
                for key, value in json.load(stream).items():
                    if ("" if value is None else str(value)) != row[key]:
                        raise ValueError(f"Summary mismatch: {run_id}/{key}")
            elif filename == "provenance.json":
                if json.load(stream)["source_sha256"] != manifest["source_sha256"]:
                    raise ValueError(f"Source revision mismatch: {run_id}")
            else:
                robots = list(csv.DictReader(io.StringIO(stream.read().decode())))
                if len(robots) != int(row["n"]):
                    raise ValueError(f"Missing robot records: {run_id}")
                for robot in robots:
                    if int(robot["waits"]) + int(robot["moves"]) != int(
                        robot["capped_arrival"]
                    ):
                        raise ValueError(f"Inconsistent robot accounting: {run_id}")
    if len(seen) != 3 * len(rows):
        raise ValueError(f"Incomplete raw archive: {directory}")
    print(
        f"Verified {directory}: {len(rows)} runs, {len(manifest['sha256'])} artifacts"
    )


if __name__ == "__main__":
    for path in sorted(Path("results").glob("*/manifest.json")):
        verify(path.parent)
