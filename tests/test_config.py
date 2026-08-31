from pathlib import Path

import pytest

from mqtt_validator.config import ConfigurationError, load_suite


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mvp_suite_has_ten_traceable_cases() -> None:
    suite = load_suite(PROJECT_ROOT / "scenarios" / "mvp.yaml")

    assert len(suite.scenarios) == 10
    assert len({case.id for case in suite.scenarios}) == 10
    assert all(case.id.startswith("TC-MQTT-") for case in suite.scenarios)
    assert all(case.requirement_id.startswith("REQ-MQTT-") for case in suite.scenarios)


def test_rejects_invalid_qos(tmp_path: Path) -> None:
    scenario_file = tmp_path / "invalid.yaml"
    scenario_file.write_text(
        """
version: 1
suite:
  name: invalid
broker: {}
scenarios:
  - id: TC-BAD-001
    requirement_id: REQ-BAD
    name: bad qos
    kind: publish_receive
    topic: bad/topic
    qos: 3
    expected:
      received_count: 1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="qos"):
        load_suite(scenario_file)


def test_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    scenario_file = tmp_path / "duplicate.yaml"
    scenario_file.write_text(
        """
version: 1
suite:
  name: duplicate
scenarios:
  - &case
    id: TC-SAME
    requirement_id: REQ-ONE
    name: first
    kind: expected_timeout
    topic: one
    expected: {received_count: 0}
  - <<: *case
    name: second
    topic: two
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unique"):
        load_suite(scenario_file)
