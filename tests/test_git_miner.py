from __future__ import annotations

from conftest import PYTEST_CMD, git

from placebo_cli.tasks.git_miner import build_prompt, is_test_file, list_candidates, mine_tasks
from placebo_cli.tasks.model import Task, expand_check, load_tasks, save_tasks


def test_is_test_file():
    assert is_test_file("tests/test_x.py")
    assert is_test_file("pkg/foo_test.go")
    assert is_test_file("src/a.test.ts")
    assert is_test_file("src/__tests__/a.js")
    assert not is_test_file("src/testing_utils.py")
    assert not is_test_file("calc.py")


def test_candidates_need_source_and_tests(repo):
    cands = list_candidates(repo, limit=10)
    subjects = [c.subject for c in cands]
    assert subjects == ["Add neg()", "Add sub()", "Add mul()"]
    assert cands[0].test_files == ["tests/test_neg.py"]
    assert "calc.py" in cands[0].source_files


def test_mine_with_validation(repo):
    tasks, rejected = mine_tasks(repo, max_tasks=2, test_cmd=PYTEST_CMD)
    assert len(tasks) == 2, rejected
    t = tasks[0]
    assert t.source == "git" and t.golden and t.base != t.golden
    assert "Do not modify the test files" in t.prompt
    assert expand_check(t.checks[0], t).endswith("tests/test_neg.py")


def test_validation_rejects_commits_whose_tests_already_pass(repo, tmp_path):
    # a commit that adds a test for existing behaviour + touches source trivially
    (repo / "calc.py").write_text((repo / "calc.py").read_text() + "\n# comment\n")
    (repo / "tests" / "test_add2.py").write_text("from calc import add\n\ndef test_add2():\n    assert add(1, 1) == 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "More add tests")
    tasks, rejected = mine_tasks(repo, max_tasks=1, test_cmd=PYTEST_CMD)
    assert tasks and tasks[0].prompt.startswith("Add neg()")
    assert any("already pass" in why for _, why in rejected)


def test_task_yaml_roundtrip(tmp_path):
    tasks = [Task(id="a", prompt="do a", checks=["echo ok"]), Task(id="b", prompt="do b", setup=["true"])]
    p = tmp_path / "tasks.yaml"
    save_tasks(tasks, p)
    loaded = load_tasks(p)
    assert [t.id for t in loaded] == ["a", "b"]
    assert loaded[1].setup == ["true"]


def test_build_prompt_contains_subject_and_files():
    from placebo_cli.tasks.git_miner import Candidate

    c = Candidate("s", "p", "Fix parser", "Handles empty input.", ["tests/test_p.py"], ["p.py"])
    prompt = build_prompt(c)
    assert prompt.startswith("Fix parser") and "tests/test_p.py" in prompt
