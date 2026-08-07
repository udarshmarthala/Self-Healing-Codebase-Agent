import os
import sys
from pathlib import Path

from healer.tools.sandbox import (
    DEFAULT_IGNORES,
    SandboxResult,
    copy_tree,
    gitignored_dirs,
    is_ignored,
    measure_tree,
    rewrite_failures,
    rewrite_paths,
    run_tests_isolated,
    sandbox,
)


def build_repo(root: Path) -> Path:
    """A small repo with one real file and several ignorable directories."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text("VALUE = 1\n")
    (root / "test_mod.py").write_text("from mod import VALUE\n\ndef test_v(): assert VALUE == 1\n")
    for junk in ("__pycache__", "node_modules", ".git"):
        (root / junk).mkdir()
        (root / junk / "junk.bin").write_text("x" * 1000)
    return root


def test_is_ignored_matches_defaults():
    assert is_ignored("node_modules")
    assert is_ignored(".git")
    assert not is_ignored("src")


def test_measure_tree_skips_ignored_dirs(tmp_path):
    repo = build_repo(tmp_path / "repo")
    files, total = measure_tree(str(repo))
    assert files == 2  # mod.py + test_mod.py only
    assert total == len("VALUE = 1\n") + len(
        "from mod import VALUE\n\ndef test_v(): assert VALUE == 1\n"
    )


def test_measure_tree_bails_out_early_past_the_limit(tmp_path):
    repo = tmp_path / "big"
    repo.mkdir()
    for n in range(20):
        (repo / f"f{n}.txt").write_text("x" * 1000)
    _, total = measure_tree(str(repo), limit_bytes=2000)
    assert total > 2000
    assert total < 20000  # stopped well before walking everything


def test_measure_tree_counts_symlinks_without_following(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_text("y" * 5000)
    os.symlink(outside, repo / "link.bin")
    files, total = measure_tree(str(repo))
    assert files == 1
    assert total == 0  # the 5000 bytes outside the repo are not counted


def test_copy_tree_copies_sources_and_skips_ignored(tmp_path):
    repo = build_repo(tmp_path / "repo")
    dest = tmp_path / "copy"
    copy_tree(str(repo), str(dest))

    assert (dest / "mod.py").read_text() == "VALUE = 1\n"
    for junk in DEFAULT_IGNORES[:3]:
        assert not (dest / junk).exists()


def test_copy_tree_preserves_symlinks_as_links(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.txt").write_text("data")
    os.symlink(repo / "real.txt", repo / "link.txt")

    dest = tmp_path / "copy"
    copy_tree(str(repo), str(dest))
    assert (dest / "link.txt").is_symlink()


def test_sandbox_yields_a_working_copy(tmp_path):
    repo = build_repo(tmp_path / "repo")
    with sandbox(str(repo)) as box:
        assert box.used
        assert box.path is not None
        assert (Path(box.path) / "mod.py").exists()
        recorded = box.path
    assert not Path(recorded).exists()  # cleaned up


def test_sandbox_edits_do_not_touch_the_real_repo(tmp_path):
    repo = build_repo(tmp_path / "repo")
    with sandbox(str(repo)) as box:
        (Path(box.path) / "mod.py").write_text("VALUE = 999\n")
    assert (repo / "mod.py").read_text() == "VALUE = 1\n"


def test_sandbox_cleans_up_after_an_exception(tmp_path):
    repo = build_repo(tmp_path / "repo")
    recorded = None
    try:
        with sandbox(str(repo)) as box:
            recorded = box.path
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert recorded is not None
    assert not Path(recorded).exists()


def test_sandbox_declines_a_repo_over_the_limit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "big.bin").write_text("x" * 200_000)
    with sandbox(str(repo), max_mb=0) as box:
        assert not box.used
        assert box.path is None
        assert "exceeds" in box.reason
        assert "not used" in box.summary()


def test_rewrite_paths_maps_output_back_to_the_repo():
    text = "File \"/tmp/healer-sandbox-x/repo/mod.py\", line 3"
    assert rewrite_paths(text, "/tmp/healer-sandbox-x/repo", "/work/repo") == (
        'File "/work/repo/mod.py", line 3'
    )


def test_rewrite_paths_without_a_sandbox_is_identity():
    assert rewrite_paths("unchanged", "", "/work/repo") == "unchanged"


def test_rewrite_failures_maps_every_entry():
    failures = ["/box/repo/test_a.py::test_x", "/box/repo/test_b.py::test_y"]
    assert rewrite_failures(failures, "/box/repo", "/real") == [
        "/real/test_a.py::test_x",
        "/real/test_b.py::test_y",
    ]


def test_run_tests_isolated_passes_and_leaves_no_trace(tmp_path):
    repo = build_repo(tmp_path / "repo")
    result, box = run_tests_isolated(
        f"{sys.executable} -m pytest test_mod.py -p no:cacheprovider", str(repo)
    )
    assert result.exit_code == 0
    assert box.used
    assert not Path(box.path).exists()
    # The suite ran elsewhere, so the real repo gained no pytest artifacts.
    assert not (repo / ".pytest_cache").exists()


def test_run_tests_isolated_reports_failures_with_real_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_bad.py").write_text("def test_broken(): assert False\n")

    result, box = run_tests_isolated(
        f"{sys.executable} -m pytest test_bad.py -p no:cacheprovider", str(repo)
    )
    assert result.exit_code != 0
    assert box.path is not None
    assert box.path not in result.output  # sandbox path never leaks to the diagnoser


def test_run_tests_isolated_falls_back_when_sandbox_declined(tmp_path):
    repo = build_repo(tmp_path / "repo")
    result, box = run_tests_isolated(
        f"{sys.executable} -m pytest test_mod.py -p no:cacheprovider", str(repo), max_mb=0
    )
    assert not box.used
    assert result.exit_code == 0  # still ran, just in place


def test_sandbox_result_summary_when_used():
    result = SandboxResult(path="/tmp/box", used=True, files_copied=3, bytes_copied=1024 * 1024)
    assert "3 file(s)" in result.summary()
    assert "1.0 MB" in result.summary()


def test_gitignored_dirs_reads_plain_directory_entries(tmp_path):
    (tmp_path / ".gitignore").write_text("build/\nfixtures/large/\n*.log\ncache/\n!keep/\n#c/\n")
    assert gitignored_dirs(str(tmp_path)) == ("build", "cache")


def test_gitignored_dirs_without_a_gitignore(tmp_path):
    assert gitignored_dirs(str(tmp_path)) == ()


def test_sandbox_skips_gitignored_directories(tmp_path):
    repo = build_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("scratch/\n")
    (repo / "scratch").mkdir()
    (repo / "scratch" / "big.bin").write_text("z" * 5000)

    with sandbox(str(repo)) as box:
        assert not (Path(box.path) / "scratch").exists()
        assert (Path(box.path) / "mod.py").exists()
