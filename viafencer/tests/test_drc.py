from __future__ import annotations

from viafencer.drc import diff_drc, parse_drc_report


def test_parse_drc_report_extracts_violations_and_severity_counts() -> None:
    snapshot = parse_drc_report(
        {
            "violations": [
                {"severity": "error", "description": "clearance", "items": ["a", "b"]},
                {"severity": "warning", "description": "silk", "items": ["c"]},
            ]
        }
    )

    assert len(snapshot.violations) == 2
    assert snapshot.severity_counts == {"error": 1, "warning": 1}


def test_diff_drc_reports_added_and_removed_violations() -> None:
    before = parse_drc_report(
        {
            "violations": [
                {"severity": "error", "description": "existing", "items": ["a"]},
            ]
        }
    )
    after = parse_drc_report(
        {
            "violations": [
                {"severity": "error", "description": "existing", "items": ["a"]},
                {"severity": "error", "description": "new", "items": ["b"]},
            ]
        }
    )

    diff = diff_drc(before, after)

    assert diff.changed
    assert diff.before_count == 1
    assert diff.after_count == 2
    assert diff.added_count == 1
    assert diff.removed_count == 0
