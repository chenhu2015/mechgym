"""Summarize a Harbor job directory: mean reward per task type with a 95% bootstrap interval.

    python scripts/summarize_job.py <jobs_dir>/<job_name> [<job2> ...] [--per-task] [--seed 0]
        [--drop-exception ApiRateLimitError]

Task type is the task name without its numeric suffix (``t0a-cyl-00002`` -> ``t0a-cyl``). Trials
that ended with an exception and no verifier reward are listed separately and counted as reward 0
in the "all" columns; rerun them before reporting if the exception was an infrastructure failure.

The interval is a two-level (cluster) bootstrap: resample tasks within the type, then trials within
each resampled task, so it reflects variation across task instances as well as across attempts.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np


def load(job: Path) -> list[dict]:
    rows = []
    for f in sorted(job.glob("*/result.json")):
        r = json.loads(f.read_text())
        task = r["task_name"].split("/")[-1]
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        exc = (r.get("exception_info") or {}).get("exception_type")
        ar = r.get("agent_result") or {}
        rows.append({"trial": r["trial_name"], "task": task, "type": re.sub(r"-\d+$", "", task),
                     "reward": reward, "exception": exc, "cost_usd": ar.get("cost_usd"),
                     "input_tokens": ar.get("n_input_tokens"), "output_tokens": ar.get("n_output_tokens")})
    return rows


def cluster_bootstrap(by_task: dict[str, list[float]], rng: np.random.Generator, n: int = 10000):
    tasks = list(by_task)
    if not tasks:
        return float("nan"), float("nan")
    means = np.empty(n)
    for b in range(n):
        picked = rng.choice(len(tasks), len(tasks))
        vals = []
        for t in picked:
            x = by_task[tasks[t]]
            vals.append(np.mean(rng.choice(x, len(x))))
        means[b] = np.mean(vals)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", type=Path, nargs="+", help="one or more job directories, pooled")
    ap.add_argument("--per-task", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--drop-exception", action="append", default=[],
                    help="drop trials that ended with this exception type (infrastructure failures, "
                         "e.g. ApiRateLimitError), whatever their reward; may be repeated")
    args = ap.parse_args()
    rows = [r for j in args.jobs for r in load(j)]
    dropped = [r for r in rows if r["exception"] in args.drop_exception]
    rows = [r for r in rows if r["exception"] not in args.drop_exception]
    args.job = Path("+".join(j.name for j in args.jobs))
    rng = np.random.default_rng(args.seed)
    types = defaultdict(lambda: defaultdict(list))
    for r in rows:
        types[r["type"]][r["task"]].append(0.0 if r["reward"] is None else float(r["reward"]))

    print(f"job: {args.job.name}   trials: {len(rows)}   "
          f"with reward: {sum(r['reward'] is not None for r in rows)}   dropped: {len(dropped)}")
    print()
    print("| task type | tasks | trials | mean reward | 95% CI | solved (reward 1) |")
    print("|---|---|---|---|---|---|")
    for t in sorted(types):
        by_task = types[t]
        allv = [v for x in by_task.values() for v in x]
        lo, hi = cluster_bootstrap(by_task, rng)
        mean = np.mean([np.mean(x) for x in by_task.values()])
        print(f"| {t} | {len(by_task)} | {len(allv)} | {mean:.2f} | [{lo:.2f}, {hi:.2f}] | "
              f"{sum(v >= 1.0 for v in allv)}/{len(allv)} |")
    by_task_all = {k: v for t in types.values() for k, v in t.items()}
    lo, hi = cluster_bootstrap(by_task_all, rng)
    mean = np.mean([np.mean(x) for x in by_task_all.values()])
    n_all = sum(len(v) for v in by_task_all.values())
    solved = sum(v >= 1.0 for x in by_task_all.values() for v in x)
    print(f"| **all** | {len(by_task_all)} | {n_all} | {mean:.2f} | [{lo:.2f}, {hi:.2f}] | {solved}/{n_all} |")

    cost = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    if cost:
        print(f"\nagent cost at API prices: total ${sum(cost):.2f}, median ${np.median(cost):.2f} per trial")
    exc = [r for r in rows if r["exception"]]
    if exc:
        print("\nexceptions:")
        for r in exc:
            print(f"- {r['trial']}: {r['exception']} (reward {r['reward']})")
    if args.per_task:
        print("\n| task | rewards |")
        print("|---|---|")
        for k in sorted(by_task_all):
            print(f"| {k} | {' '.join(f'{v:.2f}' for v in by_task_all[k])} |")


if __name__ == "__main__":
    main()
