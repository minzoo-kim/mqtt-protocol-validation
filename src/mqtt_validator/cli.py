from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from mqtt_validator.config import ConfigurationError, load_suite
from mqtt_validator.reporters import write_all_reports
from mqtt_validator.runner import SuiteRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mqtt-validate",
        description="Run requirement-traceable MQTT validation scenarios.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run a YAML scenario suite")
    run.add_argument("scenario_file", type=Path)
    run.add_argument("--host", help="override broker host from YAML")
    run.add_argument("--port", type=int, help="override broker port from YAML")
    run.add_argument("--output-dir", type=Path, default=Path("reports"))
    run.add_argument(
        "--allow-failures",
        action="store_true",
        help="write failed reports but return exit code 0",
    )
    return parser


def run_command(args: argparse.Namespace) -> int:
    try:
        suite = load_suite(args.scenario_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 2

    if args.host or args.port:
        suite = replace(
            suite,
            broker=replace(
                suite.broker,
                host=args.host or suite.broker.host,
                port=args.port or suite.broker.port,
            ),
        )

    print(
        f"Running {len(suite.scenarios)} scenarios against "
        f"{suite.broker.host}:{suite.broker.port}"
    )
    report = SuiteRunner(suite).run()
    paths = write_all_reports(report, args.output_dir)
    print(
        f"Result: {report.passed} passed, {report.failed} failed, "
        f"total {len(report.results)}"
    )
    for name, path in paths.items():
        print(f"{name.upper()}: {path.resolve()}")
    if report.failed and not args.allow_failures:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    parser.error(f"unsupported command: {args.command}")
    return 2
