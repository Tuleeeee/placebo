from __future__ import annotations

from placebo_cli.stats import Trial, compare, summarize


def trials_for(pattern: dict[str, dict[str, list[bool]]], activated: bool | None = True) -> list[Trial]:
    out = []
    for task, arms in pattern.items():
        for arm, results in arms.items():
            for r in results:
                act = activated if arm in ("treatment", "sham") else None
                out.append(Trial(task, arm, r, tokens=1000 if arm == "baseline" else 1500, cost=0.1, duration_ms=1000, activated=act))
    return out


def test_helps():
    p = {f"t{i}": {"baseline": [False, False, False], "treatment": [True, True, True], "sham": [False, False, False]} for i in range(8)}
    s = summarize(trials_for(p), ["baseline", "treatment", "sham"])
    assert s["verdict"]["label"] == "HELPS"
    c = s["comparisons"]["treatment_vs_baseline"]
    assert c["diff"] == 1.0 and c["ci95"]["low"] > 0
    assert c["tokens_diff"] == 500


def test_hurts():
    p = {f"t{i}": {"baseline": [True, True], "treatment": [False, False]} for i in range(6)}
    assert summarize(trials_for(p), ["baseline", "treatment"])["verdict"]["label"] == "HURTS"


def test_placebo_equivalence():
    # half the tasks pass in both arms, half fail in both: zero difference, no ceiling/floor
    p = {}
    for i in range(10):
        r = i % 2 == 0
        p[f"t{i}"] = {"baseline": [r, r, r], "treatment": [r, r, r], "sham": [r, r, r]}
    s = summarize(trials_for(p), ["baseline", "treatment", "sham"])
    assert s["verdict"]["label"] == "PLACEBO"


def test_not_activated_wins_over_everything():
    p = {f"t{i}": {"baseline": [False], "treatment": [True]} for i in range(6)}
    s = summarize(trials_for(p, activated=False), ["baseline", "treatment"])
    assert s["verdict"]["label"] == "NOT_ACTIVATED"


def test_ceiling_is_inconclusive_not_placebo():
    p = {f"t{i}": {"baseline": [True, True], "treatment": [True, True]} for i in range(10)}
    s = summarize(trials_for(p), ["baseline", "treatment"])
    assert s["verdict"]["label"] == "INCONCLUSIVE"
    assert "too easy" in s["verdict"]["reasons"][0]


def test_helps_vs_baseline_but_not_sham_is_inconclusive():
    p = {f"t{i}": {"baseline": [False, False], "treatment": [True, True], "sham": [True, True]} for i in range(8)}
    s = summarize(trials_for(p), ["baseline", "treatment", "sham"])
    assert s["verdict"]["label"] == "INCONCLUSIVE"
    assert "sham" in s["verdict"]["reasons"][0]


def test_too_few_tasks_for_equivalence():
    p = {f"t{i}": {"baseline": [i % 2 == 0], "treatment": [i % 2 == 0]} for i in range(3)}
    s = summarize(trials_for(p), ["baseline", "treatment"])
    assert s["verdict"]["label"] == "INCONCLUSIVE"


def test_noisy_small_sample_is_inconclusive():
    import random

    rng = random.Random(1)
    p = {f"t{i}": {"baseline": [rng.random() < 0.5 for _ in range(2)],
                   "treatment": [rng.random() < 0.5 for _ in range(2)]} for i in range(6)}
    s = summarize(trials_for(p), ["baseline", "treatment"])
    assert s["verdict"]["label"] in ("INCONCLUSIVE",)


def test_compare_is_deterministic_with_seed():
    p = {f"t{i}": {"baseline": [i % 3 == 0, True], "treatment": [True, i % 2 == 0]} for i in range(7)}
    t = trials_for(p)
    a = compare(t, "treatment", "baseline", seed=5)
    b = compare(t, "treatment", "baseline", seed=5)
    assert a == b
