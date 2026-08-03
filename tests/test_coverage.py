import json
import textwrap

from healer.tools.coverage import (
    CoverageResult,
    FileCoverage,
    parse_cobertura_xml,
    parse_coverage_json,
    parse_term_missing,
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
