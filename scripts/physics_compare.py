#!/usr/bin/env python3
"""Compare kart physics between runs at different frame rates.

Each input is a BB_PHYSICS_LOG file written during a physics test (F10 with BB_PAD_REPLAY,
see README "Physics test"). The first file is the reference, normally the 60 FPS run. The
runs are lined up by distance driven, not by time, so a run that is slower on grass shows up
as more time to reach the same point on the track.

    python scripts/physics_compare.py fisica-60.csv fisica-144.csv fisica-144-sin.csv
"""
import argparse
import csv
import math
import sys


def load(path, kart):
    """Rows of the last run in the file for one kart, one row per frame."""
    runs, header, label = [], None, ""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("# run"):
                runs.append((line[5:].strip(), []))
                header = None
                continue
            if not runs:
                continue
            if header is None:
                header = next(csv.reader([line]))
                continue
            values = next(csv.reader([line]))
            if len(values) == len(header):
                runs[-1][1].append(dict(zip(header, values)))
    if not runs:
        sys.exit(f"{path}: no run found (was BB_PHYSICS_LOG set and F10 pressed?)")
    label, rows = runs[-1]
    rows = [r for r in rows if r["kart"] == str(kart)]
    if not rows:
        sys.exit(f"{path}: kart {kart} has no rows")
    frames, last = [], None
    time = distance = 0.0
    for r in rows:
        where = (float(r["x"]), float(r["y"]), float(r["z"]))
        dt = float(r["dt"])
        if last is not None and where == last:
            continue  # the same frame called e4a220 again: count its time once
        if last is not None:
            distance += math.dist(where, last)
        if 0 < dt < 1:
            time += dt
        last = where
        frames.append({"t": time, "d": distance, "speed": float(r["out"]), "dt": dt,
                       "top": float(r["top"]), "k": float(r["k"])})
    return label, frames


def at_distance(frames, d):
    """Time and speed when the run first reaches distance d (linear between frames)."""
    for a, b in zip(frames, frames[1:]):
        if b["d"] >= d:
            span = b["d"] - a["d"]
            f = (d - a["d"]) / span if span > 0 else 0
            return a["t"] + f * (b["t"] - a["t"]), a["speed"] + f * (b["speed"] - a["speed"])
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="+", help="BB_PHYSICS_LOG files; the first is the reference")
    parser.add_argument("--kart", type=int, default=0, help="kart column (0 = first to move)")
    parser.add_argument("--marks", type=int, default=12, help="checkpoints along the route")
    args = parser.parse_args()

    runs = [(path,) + load(path, args.kart) for path in args.logs]
    common = min(frames[-1]["d"] for _, _, frames in runs)
    if common <= 0:
        sys.exit("The kart did not move in at least one run")

    print("Runs:")
    for path, label, frames in runs:
        fps = len(frames) / frames[-1]["t"] if frames[-1]["t"] > 0 else 0
        print(f"  {path}: {label}, {fps:.0f} FPS, {frames[-1]['t']:.2f} s, distance {frames[-1]['d']:.0f}")
    print(f"\nCompared over the distance all runs drove: {common:.0f}\n")

    names = [f"run {i + 1}" for i in range(len(runs))]
    print("distance  " + "  ".join(f"{n:>17}" for n in names))
    print("          " + "  ".join(f"{'time s / speed':>17}" for _ in names))
    for i in range(1, args.marks + 1):
        d = common * i / args.marks
        cells = []
        for _, _, frames in runs:
            hit = at_distance(frames, d)
            cells.append(f"{hit[0]:8.3f} / {hit[1]:6.1f}" if hit else f"{'-':>17}")
        print(f"{d:8.0f}  " + "  ".join(cells))

    end = [at_distance(frames, common * .999) for _, _, frames in runs]
    reference = end[0][0]
    print("\nTime to the end of the common distance, against run 1:")
    for i, hit in enumerate(end):
        delta = hit[0] - reference
        print(f"  run {i + 1}: {hit[0]:.3f} s ({delta:+.3f} s, {100 * delta / reference:+.2f} %)")
    print("\nWithin about 1 % is the noise of a replay (inputs land on slightly different frames).")


if __name__ == "__main__":
    main()
