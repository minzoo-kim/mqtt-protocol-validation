import os
from pathlib import Path

import pytest

from mqtt_validator.config import load_suite
from mqtt_validator.reporters import write_all_reports
from mqtt_validator.runner import SuiteRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("MQTT_INTEGRATION") != "1",
    reason="set MQTT_INTEGRATION=1 with Mosquitto running",
)
def test_full_mvp_suite_against_mosquitto(tmp_path: Path) -> None:
    suite = load_suite(PROJECT_ROOT / "scenarios" / "mvp.yaml")

    report = SuiteRunner(suite).run()
    paths = write_all_reports(report, tmp_path)

    assert report.failed == 0, [
        {
            "case_id": result.case_id,
            "error": result.error,
            "mismatches": result.mismatches,
        }
        for result in report.results
        if result.status == "failed"
    ]
    assert all(path.is_file() for path in paths.values())
