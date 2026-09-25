"""Statistics for paired A/B trials.

Design (see METHODOLOGY.md):
* The unit of analysis is the *task*. For each task we compare mean pass rates
  between two arms, so tasks act as their own controls (paired design).
* Uncertainty comes from a two-level bootstrap: resample tasks, then resample
  trials within each task and arm. That captures both "which tasks you picked"
  and agent run-to-run noise.
* HELPS / HURTS need the 95% CI to exclude zero. PLACEBO needs the 90% CI to
  fall inside ±delta (a TOST equivalence test at alpha = 0.05), so "no effect"
  is a positive finding rather than a failure to find one.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Iterable


@dataclass
class Trial:
    task: str
    arm: str
    passed: bool
    tokens: int | None = None
    cost: float | None = None
    duration_ms: int | None = None
    activated: bool | None = None  # None = not applicable


@dataclass
class Interval:
    low: float
    high: float


@dataclass
class Comparison:
    a: str
    b: str
    n_tasks: int
    diff: float  # mean(pass_a - pass_b) over tasks
    ci95: Interval
    ci90: Interval
    mcnemar_p: float | None
    tokens_diff: float | None
    tokens_ci95: Interval | None
    cost_diff: float | None
    cost_ci95: Interval | None
    time_diff_ms: float | None


@dataclass
class ArmStats:
    arm: str
    trials: int
    passed: int
    pass_rate: float
    mean_tokens: float | None
    mean_cost: float | None
    mean_duration_ms: float | None
    activation_rate: float | None


@dataclass
class Verdict:
    label: str  # HELPS | HURTS | PLACEBO | INCONCLUSIVE | NOT_ACTIVATED
    reasons: list[str] = field(default_factory=list)


def _group(trials: Iterable[Trial]) -> dict[str, dict[str, list[Trial]]]:
    g: dict[str, dict[str, list[Trial]]] = {}
    for t in trials:
        g.setdefault(t.task, {}).setdefault(t.arm, []).append(t)
    return g


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def _binom_two_sided(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    return min(1.0, 2 * tail)


def arm_stats(trials: list[Trial], arm: str) -> ArmStats:
    ts = [t for t in trials if t.arm == arm]
    n = len(ts)
    passed = sum(t.passed for t in ts)
    toks = [t.tokens for t in ts if t.tokens is not None]
    costs = [t.cost for t in ts if t.cost is not None]
    durs = [t.duration_ms for t in ts if t.duration_ms is not None]
    acts = [t.activated for t in ts if t.activated is not None]
    return ArmStats(
        arm=arm,
        trials=n,
        passed=passed,
        pass_rate=passed / n if n else float("nan"),
        mean_tokens=mean(toks) if toks else None,
        mean_cost=mean(costs) if costs else None,
        mean_duration_ms=mean(durs) if durs else None,
        activation_rate=(sum(acts) / len(acts)) if acts else None,
    )


def compare(trials: list[Trial], a: str, b: str, n_boot: int = 4000, seed: int = 0) -> Comparison | None:
    g = _group(trials)
    tasks = [t for t, arms in g.items() if arms.get(a) and arms.get(b)]
    if not tasks:
        return None
    rng = random.Random(seed)

    def per_task(metric: str, task: str, arm: str, sample: list[Trial] | None = None) -> float | None:
        ts = sample if sample is not None else g[task][arm]
        vals = [getattr(t, metric) for t in ts]
        vals = [float(v) for v in vals if v is not None]
        return mean(vals) if vals else None

    def diffs(metric: str) -> list[float]:
        out = []
        for t in tasks:
            va, vb = per_task(metric, t, a), per_task(metric, t, b)
            if va is not None and vb is not None:
                out.append(va - vb)
        return out

    d_pass = diffs("passed")
    point = mean(d_pass)

    boot: list[float] = []
    for _ in range(n_boot):
        acc = []
        for _ in range(len(tasks)):
            t = tasks[rng.randrange(len(tasks))]
            ta, tb = g[t][a], g[t][b]
            sa = [ta[rng.randrange(len(ta))] for _ in ta]
            sb = [tb[rng.randrange(len(tb))] for _ in tb]
            acc.append(mean(x.passed for x in sa) - mean(x.passed for x in sb))
        boot.append(mean(acc))

    def boot_simple(vals: list[float]) -> Interval | None:
        if not vals:
            return None
        ms = []
        for _ in range(min(n_boot, 2000)):
            ms.append(mean(vals[rng.randrange(len(vals))] for _ in vals))
        return Interval(_pct(ms, 0.025), _pct(ms, 0.975))

    # McNemar on per-task majority outcomes
    b_cnt = c_cnt = 0
    for t in tasks:
        pa = mean(x.passed for x in g[t][a]) >= 0.5
        pb = mean(x.passed for x in g[t][b]) >= 0.5
        if pa and not pb:
            b_cnt += 1
        elif pb and not pa:
            c_cnt += 1

    d_tok = diffs("tokens")
    d_cost = diffs("cost")
    d_time = diffs("duration_ms")
    return Comparison(
        a=a,
        b=b,
        n_tasks=len(tasks),
        diff=point,
        ci95=Interval(_pct(boot, 0.025), _pct(boot, 0.975)),
        ci90=Interval(_pct(boot, 0.05), _pct(boot, 0.95)),
        mcnemar_p=_binom_two_sided(b_cnt, c_cnt),
        tokens_diff=mean(d_tok) if d_tok else None,
        tokens_ci95=boot_simple(d_tok),
        cost_diff=mean(d_cost) if d_cost else None,
        cost_ci95=boot_simple(d_cost),
        time_diff_ms=mean(d_time) if d_time else None,
    )


def verdict(
    trials: list[Trial],
    tvb: Comparison | None,
    tvs: Comparison | None,
    delta: float = 0.05,
    min_activation: float = 0.3,
    min_tasks_equivalence: int = 5,
) -> Verdict:
    if tvb is None:
        return Verdict("INCONCLUSIVE", ["no paired results yet"])
    reasons: list[str] = []
    treat = arm_stats(trials, "treatment")
    if treat.activation_rate is not None and treat.activation_rate < min_activation:
        return Verdict("NOT_ACTIVATED", [
            f"the skill was activated in only {treat.activation_rate:.0%} of treatment trials "
            f"(< {min_activation:.0%}); the comparison does not measure its content",
        ])
    base = arm_stats(trials, "baseline")
    both = [x for x in (treat.pass_rate, base.pass_rate) if not math.isnan(x)]
    ceiling = all(x == 1.0 for x in both)
    floor = all(x == 0.0 for x in both)

    if tvb.ci95.low > 0:
        if tvs is not None and not (tvs.ci95.low > 0):
            return Verdict("INCONCLUSIVE", [
                f"better than baseline ({_pp(tvb.diff)}), but not distinguishable from the sham "
                f"({_pp(tvs.diff)}, 95% CI {_pp(tvs.ci95.low)}..{_pp(tvs.ci95.high)}): the gain may come "
                "from extra context rather than the skill's content",
            ])
        return Verdict("HELPS", [f"pass rate {_pp(tvb.diff)} vs baseline (95% CI {_pp(tvb.ci95.low)}..{_pp(tvb.ci95.high)})"])
    if tvb.ci95.high < 0:
        return Verdict("HURTS", [f"pass rate {_pp(tvb.diff)} vs baseline (95% CI {_pp(tvb.ci95.low)}..{_pp(tvb.ci95.high)})"])
    if ceiling or floor:
        reasons.append("every trial " + ("passed" if ceiling else "failed") +
                       " in both arms: tasks are too easy/hard to detect an effect")
        return Verdict("INCONCLUSIVE", reasons)
    if tvb.n_tasks < min_tasks_equivalence:
        return Verdict("INCONCLUSIVE", [f"only {tvb.n_tasks} tasks; need ≥{min_tasks_equivalence} to claim no effect"])
    if -delta < tvb.ci90.low and tvb.ci90.high < delta:
        return Verdict("PLACEBO", [
            f"90% CI {_pp(tvb.ci90.low)}..{_pp(tvb.ci90.high)} lies within ±{delta * 100:.0f} pp: no meaningful effect",
        ])
    width = tvb.ci95.high - tvb.ci95.low
    return Verdict("INCONCLUSIVE", [
        f"95% CI {_pp(tvb.ci95.low)}..{_pp(tvb.ci95.high)} (width {width * 100:.0f} pp) is too wide: add tasks or trials",
    ])


def _pp(x: float) -> str:
    return f"{x * 100:+.0f} pp"


def summarize(trials: list[Trial], arms: list[str], delta: float = 0.05, seed: int = 0) -> dict:
    per_arm = [arm_stats(trials, a) for a in arms]
    tvb = compare(trials, "treatment", "baseline", seed=seed) if {"treatment", "baseline"} <= set(arms) else None
    tvs = compare(trials, "treatment", "sham", seed=seed + 1) if {"treatment", "sham"} <= set(arms) else None
    svb = compare(trials, "sham", "baseline", seed=seed + 2) if {"sham", "baseline"} <= set(arms) else None
    v = verdict(trials, tvb, tvs, delta=delta)
    per_task: dict[str, dict[str, list[int]]] = {}
    for t in trials:
        per_task.setdefault(t.task, {}).setdefault(t.arm, [0, 0])
        per_task[t.task][t.arm][0] += int(t.passed)
        per_task[t.task][t.arm][1] += 1
    return {
        "arms": [asdict(s) for s in per_arm],
        "comparisons": {
            k: asdict(c) for k, c in (("treatment_vs_baseline", tvb), ("treatment_vs_sham", tvs), ("sham_vs_baseline", svb))
            if c is not None
        },
        "verdict": asdict(v),
        "per_task": per_task,
        "delta": delta,
    }
