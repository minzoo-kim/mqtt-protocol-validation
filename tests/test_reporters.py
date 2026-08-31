import json
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from mqtt_validator.models import (
    BrokerConfig,
    EventRecord,
    RunReport,
    ScenarioResult,
)
from mqtt_validator.reporters import write_all_reports


def _report() -> RunReport:
    now = datetime.now(timezone.utc).isoformat()
    passed = ScenarioResult(
        case_id="TC-MQTT-001",
        requirement_id="REQ-MQTT-QOS0",
        name="pass",
        status="passed",
        started_at=now,
        duration_ms=12,
        expected={"received_count": 1},
        observed={"received_count": 1},
        events=[
            EventRecord(
                timestamp=now,
                case_id="TC-MQTT-001",
                requirement_id="REQ-MQTT-QOS0",
                stage="case_finished",
                details={"status": "passed"},
            )
        ],
    )
    failed = ScenarioResult(
        case_id="TC-MQTT-002",
        requirement_id="REQ-MQTT-QOS1",
        name="fail",
        status="failed",
        started_at=now,
        duration_ms=20,
        expected={"delivered_qos": 2},
        observed={"delivered_qos": 1},
        mismatches=["delivered_qos: expected 2, observed 1"],
    )
    return RunReport(
        run_id="run-test",
        suite_name="test suite",
        suite_description="report fixture",
        started_at=now,
        finished_at=now,
        broker=BrokerConfig(),
        results=[passed, failed],
    )


def test_writes_json_junit_and_jsonl(tmp_path: Path) -> None:
    paths = write_all_reports(_report(), tmp_path)

    assert set(paths) == {"json", "junit", "jsonl"}
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "success": False,
    }
    junit = ET.parse(paths["junit"]).getroot()
    assert junit.attrib["tests"] == "2"
    assert junit.attrib["failures"] == "1"
    records = [
        json.loads(line)
        for line in paths["jsonl"].read_text(encoding="utf-8").splitlines()
    ]
    assert any(record["stage"] == "case_result" for record in records)
