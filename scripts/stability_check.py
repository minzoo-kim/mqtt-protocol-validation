from __future__ import annotations

import argparse
import time
from pathlib import Path

from mqtt_validator.config import load_suite
from mqtt_validator.runner import SuiteRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat the MQTT MVP suite to detect state leakage or race failures."
    )
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    suite = load_suite(PROJECT_ROOT / "scenarios" / "mvp.yaml")
    failures: list[dict[str, object]] = []
    started = time.perf_counter()
    for run_number in range(1, args.runs + 1):
        report = SuiteRunner(suite).run()
        print(
            f"run {run_number:02d}/{args.runs}: "
            f"{report.passed} passed, {report.failed} failed ({report.run_id})"
        )
        failures.extend(
            {
                "run": run_number,
                "run_id": report.run_id,
                "case_id": result.case_id,
                "error": result.error,
                "mismatches": result.mismatches,
            }
            for result in report.results
            if result.status == "failed"
        )

    elapsed = time.perf_counter() - started
    print(
        f"completed {args.runs * len(suite.scenarios)} case executions "
        f"in {elapsed:.2f}s; failures={len(failures)}"
    )
    if failures:
        for failure in failures:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
