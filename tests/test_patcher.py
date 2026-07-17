import pytest
from pathlib import Path
from healer.tools.patcher import apply_patch, write_file, verify_patch_applies, PatchError


def test_write_file(tmp_path):
    write_file(str(tmp_path), "src/foo.py", "x = 1\n")
    assert (tmp_path / "src" / "foo.py").read_text() == "x = 1\n"


def test_write_file_creates_dirs(tmp_path):
    write_file(str(tmp_path), "deep/nested/bar.py", "pass\n")
    assert (tmp_path / "deep" / "nested" / "bar.py").exists()


def test_apply_patch(tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    apply_patch(str(tmp_path), diff)
    assert target.read_text() == "x = 2\n"


def test_apply_patch_bad_diff(tmp_path):
    (tmp_path / "foo.py").write_text("x = 1\n")
    with pytest.raises(PatchError):
        apply_patch(str(tmp_path), "not a real diff")


def test_verify_patch_applies(tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    assert verify_patch_applies(str(tmp_path), diff)
    assert not verify_patch_applies(str(tmp_path), "garbage")
