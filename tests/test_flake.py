import sys
from pathlib import Path

from healer.tools.flake import (
    FlakeReport,
    FlakeVerdict,
    build_rerun_command,
    check_failures,
    check_test,
    is_node_id,
    supports_selection,
)


def test_verdict_mixed_results_is_flaky():
    assert FlakeVerdict("t.py::test_a", runs=3, passes=1).is_flaky


def test_verdict_all_failing_is_consistent():
    verdict = FlakeVerdict("t.py::test_a", runs=3, passes=0)
    assert verdict.is_consistent_failure
    assert not verdict.is_flaky
    assert verdict.failures == 3


def test_verdict_all_passing_is_not_flaky():
    """Passing every re-run means the earlier failure was already fixed or
    order-dependent — not the same thing as flaky."""
    verdict = FlakeVerdict("t.py::test_a", runs=3, passes=3)
    assert not verdict.is_flaky
    assert not verdict.is_consistent_failure


def test_verdict_single_run_cannot_be_flaky():
    assert not FlakeVerdict("t.py::test_a", runs=1, passes=0).is_flaky


def test_verdict_str_describes_each_case():
    assert "FLAKY" in str(FlakeVerdict("t::a", 3, 1))
    assert "consistent failure" in str(FlakeVerdict("t::a", 3, 0))
    assert "passed on re-run" in str(FlakeVerdict("t::a", 3, 3))


def test_report_splits_flaky_from_real():
    report = FlakeReport(verdicts=[
        FlakeVerdict("t::flaky", 3, 1),
        FlakeVerdict("t::broken", 3, 0),
        FlakeVerdict("t::fine", 3, 3),
    ])
    assert report.flaky == ["t::flaky"]
    assert report.real_failures == ["t::broken"]
    assert "1 flaky" in report.summary()


def test_supports_selection_requires_pytest():
    assert supports_selection("pytest -v")
    assert not supports_selection("npm test")


def test_is_node_id_recognises_pytest_ids():
    assert is_node_id("tests/test_a.py::test_x")
    assert not is_node_id("some error line")
    assert not is_node_id("TIMEOUT")


def test_build_rerun_command_strips_exitfirst():
    cmd = build_rerun_command("pytest -x -v", "tests/test_a.py::test_x")
    assert " -x" not in cmd
    assert '"tests/test_a.py::test_x"' in cmd


def test_check_failures_skips_non_pytest_commands():
    report = check_failures("npm test", "/tmp", ["a::b"])
    assert not report.checked
    assert "cannot select" in report.reason


def test_check_failures_disabled_by_low_retries():
    report = check_failures("pytest", "/tmp", ["a.py::b"], retries=1)
    assert not report.checked
    assert "disabled" in report.reason


def test_check_failures_ignores_unparseable_failures():
    report = check_failures("pytest", "/tmp", ["TIMEOUT", "some error"])
    assert not report.checked
    assert "node ids" in report.reason


def _write_flaky_repo(root: Path) -> Path:
    """A test that fails on its first run and passes afterwards, via a marker
    file — a deterministic stand-in for real nondeterminism."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "test_flaky.py").write_text(
        "from pathlib import Path\n"
        "MARKER = Path(__file__).parent / 'seen.txt'\n"
        "def test_sometimes():\n"
        "    first = not MARKER.exists()\n"
        "    MARKER.write_text('x')\n"
        "    assert not first\n"
    )
    return root


def test_check_test_detects_a_flaky_test(tmp_path):
    repo = _write_flaky_repo(tmp_path / "repo")
    verdict = check_test(
        f"{sys.executable} -m pytest", str(repo), "test_flaky.py::test_sometimes", retries=3
    )
    assert verdict.runs == 3
    assert verdict.passes == 2  # fails once, then passes
    assert verdict.is_flaky


def test_check_test_detects_a_consistent_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_bad.py").write_text("def test_always_fails(): assert False\n")
    verdict = check_test(
        f"{sys.executable} -m pytest", str(repo), "test_bad.py::test_always_fails", retries=3
    )
    assert verdict.passes == 0
    assert verdict.is_consistent_failure
    assert not verdict.is_flaky


def test_check_failures_separates_the_two(tmp_path):
    repo = _write_flaky_repo(tmp_path / "repo")
    (repo / "test_bad.py").write_text("def test_always_fails(): assert False\n")

    report = check_failures(
        f"{sys.executable} -m pytest",
        str(repo),
        ["test_flaky.py::test_sometimes", "test_bad.py::test_always_fails"],
        retries=3,
    )
    assert report.flaky == ["test_flaky.py::test_sometimes"]
    assert report.real_failures == ["test_bad.py::test_always_fails"]


def test_check_failures_caps_the_number_of_tests(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_bad.py").write_text("def test_always_fails(): assert False\n")
    failures = [f"test_bad.py::test_always_fails{n}" for n in range(25)]
    report = check_failures(
        f"{sys.executable} -m pytest", str(repo), failures, retries=2, max_tests=3
    )
    assert len(report.verdicts) == 3


def _flaky_and_broken_repo(root: Path) -> Path:
    repo = _write_flaky_repo(root)
    (repo / "test_broken.py").write_text("def test_real(): assert 1 == 2\n")
    return repo


def test_loop_quarantines_flaky_but_keeps_real_failures(tmp_path):
    from healer.loop import _quarantine_flaky
    from healer.state import HealerState
    from healer.tools.journal import Recorder

    repo = _flaky_and_broken_repo(tmp_path / "repo")
    state = HealerState(
        goal="g",
        target_repo=str(repo),
        test_command=f"{sys.executable} -m pytest",
        flake_retries=3,
    )
    failures = ["test_flaky.py::test_sometimes", "test_broken.py::test_real"]

    _quarantine_flaky(state, failures, Recorder(str(tmp_path / 'j.json')))

    assert state.known_flaky == {"test_flaky.py::test_sometimes"}
    assert state.actionable_failures(failures) == ["test_broken.py::test_real"]


def test_quarantine_is_skipped_when_retries_disabled(tmp_path):
    from healer.loop import _quarantine_flaky
    from healer.state import HealerState
    from healer.tools.journal import Recorder

    repo = _flaky_and_broken_repo(tmp_path / "repo")
    state = HealerState(
        goal="g", target_repo=str(repo), test_command=f"{sys.executable} -m pytest", flake_retries=0
    )
    _quarantine_flaky(state, ["test_flaky.py::test_sometimes"], Recorder(str(tmp_path / "j.json")))
    assert state.known_flaky == set()


def test_quarantine_does_not_recheck_known_flaky(tmp_path):
    """A test already known to flake must not earn more re-runs each cycle."""
    from healer.loop import _quarantine_flaky
    from healer.state import HealerState
    from healer.tools.journal import Recorder

    repo = _flaky_and_broken_repo(tmp_path / "repo")
    state = HealerState(
        goal="g", target_repo=str(repo), test_command=f"{sys.executable} -m pytest", flake_retries=3
    )
    state.record_flake_check(flaky=["test_flaky.py::test_sometimes"], real=[])
    before = len(state.flake_by_cycle)

    _quarantine_flaky(state, ["test_flaky.py::test_sometimes"], Recorder(str(tmp_path / "j.json")))
    assert len(state.flake_by_cycle) == before  # nothing re-checked
