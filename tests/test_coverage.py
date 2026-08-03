import json
import sys
import textwrap

from healer.tools.coverage import (
    CoverageResult,
    FileCoverage,
    added_lines,
    build_coverage_command,
    cleanup_artifacts,
    coverage_delta,
    measure,
    parse_cobertura_xml,
    parse_coverage_json,
    parse_term_missing,
    read_report,
    supports_coverage,
    untested_patch_lines,
)

COBERTURA = """<?xml version="1.0" ?>
<coverage>
  <packages>
    <package name="src">
      <classes>
        <class filename="src/auth.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
            <line number="3" hits="4"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def test_parse_cobertura_xml():
    result = parse_cobertura_xml(COBERTURA)
    cov = result.files["src/auth.py"]
    assert cov.statements == 3
    assert cov.missing_lines == [2]
    assert cov.covered == 2
    assert result.source == "xml"


def test_parse_cobertura_malformed_is_unavailable():
    result = parse_cobertura_xml("<coverage><not-closed>")
    assert not result.available


def test_parse_cobertura_merges_split_file_entries():
    doubled = COBERTURA.replace(
        '<class filename="src/auth.py">',
        '<class filename="src/auth.py"><lines><line number="9" hits="0"/></lines></class>'
        '<class filename="src/auth.py">',
        1,
    )
    result = parse_cobertura_xml(doubled)
    assert result.files["src/auth.py"].missing_lines == [2, 9]


def test_parse_coverage_json():
    payload = json.dumps({
        "files": {
            "src/db.py": {"summary": {"num_statements": 10}, "missing_lines": [4, 5]},
        }
    })
    result = parse_coverage_json(payload)
    cov = result.files["src/db.py"]
    assert cov.statements == 10
    assert cov.missing_lines == [4, 5]
    assert cov.rate == 0.8


def test_parse_coverage_json_infers_statements_when_summary_missing():
    payload = json.dumps({
        "files": {"a.py": {"executed_lines": [1, 2, 3], "missing_lines": [4]}}
    })
    assert parse_coverage_json(payload).files["a.py"].statements == 4


def test_parse_coverage_json_malformed_is_unavailable():
    assert not parse_coverage_json("{not json").available


def test_parse_term_missing():
    output = textwrap.dedent("""
        Name            Stmts   Miss  Cover   Missing
        ---------------------------------------------
        src/auth.py        45      6    87%   12-14, 20, 31-33
        src/db.py          10      0   100%
        ---------------------------------------------
        TOTAL              55      6    89%
    """)
    result = parse_term_missing(output)
    assert result.files["src/auth.py"].missing_lines == [12, 13, 14, 20, 31, 32, 33]
    assert result.files["src/db.py"].missing_lines == []
    assert "TOTAL" not in result.files


def test_parse_term_missing_skips_branch_markers():
    output = "a.py   10   1   90%   18->20, 25\n"
    assert parse_term_missing(output).files["a.py"].missing_lines == [25]


def test_rate_of_empty_file_is_full():
    assert FileCoverage(path="a.py", statements=0).rate == 1.0


def test_result_get_matches_by_path_suffix():
    result = CoverageResult(files={"src/auth.py": FileCoverage("src/auth.py", 5)})
    assert result.get("auth.py") is not None
    assert result.get("other.py") is None


def _result(path="src/a.py", statements=10, missing=()):
    return CoverageResult(
        files={path: FileCoverage(path=path, statements=statements, missing_lines=list(missing))}
    )


def test_supports_coverage_only_for_pytest():
    assert supports_coverage("pytest -v")
    assert not supports_coverage("npm test")


def test_build_coverage_command_adds_flags():
    cmd = build_coverage_command("pytest -v")
    assert "--cov=." in cmd
    assert "--cov-report=json:.healer-coverage.json" in cmd


def test_build_coverage_command_respects_existing_cov_flag():
    cmd = build_coverage_command("pytest --cov=src")
    assert cmd.count("--cov=") == 1
    assert "--cov=src" in cmd


def test_build_coverage_command_passes_through_non_pytest():
    assert build_coverage_command("npm test") == "npm test"


def test_coverage_delta_reports_improvement():
    delta = coverage_delta(_result(missing=[1, 2, 3, 4]), _result(missing=[1]))
    assert delta.rate_change > 0
    assert not delta.declined
    assert "↑" in delta.summary()


def test_coverage_delta_flags_decline():
    delta = coverage_delta(_result(missing=[1]), _result(missing=[1, 2, 3, 4]))
    assert delta.declined
    assert delta.files_declined == ["src/a.py"]


def test_coverage_delta_tolerates_small_drop():
    before = _result(statements=100, missing=[])
    after = _result(statements=100, missing=[1])
    assert not coverage_delta(before, after).declined


def test_coverage_delta_is_noop_when_unavailable():
    delta = coverage_delta(CoverageResult(available=False), _result())
    assert delta.rate_change == 0.0
    assert not delta.declined


DIFF = """--- a/src/a.py
+++ b/src/a.py
@@ -10,3 +10,5 @@
 context
+added_one
+added_two
 context
-removed
"""


def test_added_lines_maps_diff_to_post_patch_numbers():
    assert added_lines(DIFF) == [11, 12]


def test_added_lines_handles_multiple_hunks():
    diff = "@@ -1,2 +1,2 @@\n+first\n@@ -50,2 +60,2 @@\n+second\n"
    assert added_lines(diff) == [1, 60]


def test_untested_patch_lines_flags_unexecuted_additions():
    result = _result(missing=[11])
    assert untested_patch_lines(result, "src/a.py", DIFF) == [11]


def test_untested_patch_lines_empty_when_all_covered():
    assert untested_patch_lines(_result(missing=[99]), "src/a.py", DIFF) == []


def test_untested_patch_lines_empty_when_unavailable():
    assert untested_patch_lines(CoverageResult(available=False), "src/a.py", DIFF) == []


def test_read_report_prefers_json(tmp_path):
    (tmp_path / ".healer-coverage.json").write_text(
        json.dumps({"files": {"a.py": {"summary": {"num_statements": 2}, "missing_lines": [1]}}})
    )
    (tmp_path / "coverage.xml").write_text(COBERTURA)
    assert read_report(str(tmp_path)).source == "json"


def test_read_report_falls_back_to_xml(tmp_path):
    (tmp_path / "coverage.xml").write_text(COBERTURA)
    assert read_report(str(tmp_path)).source == "xml"


def test_read_report_missing_is_unavailable(tmp_path):
    assert not read_report(str(tmp_path)).available


def test_measure_end_to_end(tmp_path):
    (tmp_path / "mod.py").write_text("def used():\n    return 1\n\ndef unused():\n    return 2\n")
    (tmp_path / "test_mod.py").write_text("from mod import used\n\ndef test_used():\n    assert used() == 1\n")
    result = measure(f"{sys.executable} -m pytest test_mod.py -p no:cacheprovider", str(tmp_path))
    assert result.available
    cov = result.get("mod.py")
    assert cov is not None
    assert 5 in cov.missing_lines  # body of unused()


def test_measure_skips_non_pytest_suites(tmp_path):
    assert not measure("npm test", str(tmp_path)).available


def test_measure_removes_its_artifacts(tmp_path):
    (tmp_path / "mod.py").write_text("def used():\n    return 1\n")
    (tmp_path / "test_mod.py").write_text("from mod import used\n\ndef test_used():\n    assert used() == 1\n")
    measure(f"{sys.executable} -m pytest test_mod.py -p no:cacheprovider", str(tmp_path))
    assert not (tmp_path / ".healer-coverage.json").exists()
    assert not (tmp_path / ".coverage").exists()


def test_cleanup_artifacts_is_safe_when_absent(tmp_path):
    cleanup_artifacts(str(tmp_path))  # must not raise
