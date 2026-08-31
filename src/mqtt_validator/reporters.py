from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from mqtt_validator.models import RunReport


def write_json_report(report: RunReport, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def write_jsonl_events(report: RunReport, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for result in report.results:
        records.extend(
            {"run_id": report.run_id, **event.to_dict()} for event in result.events
        )
        records.append(
            {
                "run_id": report.run_id,
                "timestamp": report.finished_at,
                "case_id": result.case_id,
                "requirement_id": result.requirement_id,
                "stage": "case_result",
                "details": {
                    "status": result.status,
                    "expected": result.expected,
                    "observed": result.observed,
                    "mismatches": result.mismatches,
                    "error": result.error,
                },
            }
        )
    destination.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )
    return destination


def write_junit_report(report: RunReport, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_seconds = sum(result.duration_ms for result in report.results) / 1000
    suite = ET.Element(
        "testsuite",
        {
            "name": report.suite_name,
            "tests": str(len(report.results)),
            "failures": str(report.failed),
            "errors": "0",
            "time": f"{total_seconds:.3f}",
            "timestamp": report.started_at,
        },
    )
    properties = ET.SubElement(suite, "properties")
    ET.SubElement(properties, "property", {"name": "run_id", "value": report.run_id})

    for result in report.results:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "name": result.name,
                "classname": result.requirement_id,
                "time": f"{result.duration_ms / 1000:.3f}",
            },
        )
        ET.SubElement(
            case,
            "properties",
        ).append(
            ET.Element(
                "property",
                {"name": "case_id", "value": result.case_id},
            )
        )
        if result.status == "failed":
            detail = result.error or "; ".join(result.mismatches)
            failure = ET.SubElement(
                case,
                "failure",
                {"message": detail or "validation failed", "type": "AssertionError"},
            )
            failure.text = json.dumps(
                {
                    "expected": result.expected,
                    "observed": result.observed,
                    "mismatches": result.mismatches,
                    "error": result.error,
                },
                ensure_ascii=False,
                indent=2,
            )
        system_out = ET.SubElement(case, "system-out")
        system_out.text = json.dumps(result.observed, ensure_ascii=False)

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def write_all_reports(report: RunReport, output_dir: str | Path) -> dict[str, Path]:
    directory = Path(output_dir)
    stem = f"mqtt-report-{report.run_id}"
    return {
        "json": write_json_report(report, directory / f"{stem}.json"),
        "junit": write_junit_report(report, directory / f"{stem}.xml"),
        "jsonl": write_jsonl_events(report, directory / f"{stem}.jsonl"),
    }
