from mqtt_validator.runner import compare_expected, interpolate


def test_interpolate_replaces_only_run_id_token() -> None:
    value = {
        "topic": "validator/{run_id}/state",
        "malformed_json": '{"id":"{run_id}",',
        "nested": ["{run_id}", 7],
    }

    assert interpolate(value, "RUN-123") == {
        "topic": "validator/RUN-123/state",
        "malformed_json": '{"id":"RUN-123",',
        "nested": ["RUN-123", 7],
    }


def test_compare_expected_reports_missing_and_mismatched_fields() -> None:
    mismatches = compare_expected(
        {"count": 1, "qos": 2, "retain": True},
        {"count": 1, "qos": 1},
    )

    assert mismatches == [
        "qos: expected 2, observed 1",
        "retain: expected True, but no value was observed",
    ]


def test_compare_expected_accepts_observed_diagnostics() -> None:
    assert compare_expected(
        {"received_count": 1},
        {"received_count": 1, "payload": {"value": 10}, "dup": False},
    ) == []
