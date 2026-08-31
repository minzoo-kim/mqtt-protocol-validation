from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mqtt_validator.models import (
    VALID_SCENARIO_KINDS,
    BrokerConfig,
    Scenario,
    SuiteConfig,
)


class ConfigurationError(ValueError):
    """Raised when a scenario suite cannot be validated."""


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigurationError(f"{context}: missing required field '{key}'")
    return mapping[key]


def _validate_qos(value: Any, field_name: str, case_id: str) -> int:
    if not isinstance(value, int) or value not in (0, 1, 2):
        raise ConfigurationError(
            f"{case_id}: '{field_name}' must be one of 0, 1, or 2"
        )
    return value


def _parse_scenario(raw: Any, index: int) -> Scenario:
    context = f"scenario[{index}]"
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{context}: must be a mapping")

    case_id = str(_required(raw, "id", context))
    kind = str(_required(raw, "kind", case_id))
    if kind not in VALID_SCENARIO_KINDS:
        valid = ", ".join(sorted(VALID_SCENARIO_KINDS))
        raise ConfigurationError(f"{case_id}: unsupported kind '{kind}'; use {valid}")

    qos = _validate_qos(raw.get("qos", 0), "qos", case_id)
    subscribe_qos = _validate_qos(
        raw.get("subscribe_qos", qos), "subscribe_qos", case_id
    )
    timeout_s = raw.get("timeout_s", 2.0)
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ConfigurationError(f"{case_id}: 'timeout_s' must be greater than zero")

    expected = raw.get("expected", {})
    if not isinstance(expected, dict) or not expected:
        raise ConfigurationError(f"{case_id}: 'expected' must be a non-empty mapping")
    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise ConfigurationError(f"{case_id}: 'options' must be a mapping")

    return Scenario(
        id=case_id,
        requirement_id=str(_required(raw, "requirement_id", case_id)),
        name=str(_required(raw, "name", case_id)),
        kind=kind,
        topic=str(_required(raw, "topic", case_id)),
        qos=qos,
        subscribe_qos=subscribe_qos,
        timeout_s=float(timeout_s),
        payload=raw.get("payload"),
        expected=expected,
        publish_topic=(
            str(raw["publish_topic"]) if raw.get("publish_topic") else None
        ),
        options=options,
    )


def load_suite(path: str | Path) -> SuiteConfig:
    source = Path(path)
    if not source.is_file():
        raise ConfigurationError(f"scenario file does not exist: {source}")

    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {source}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("suite document must be a mapping")
    if raw.get("version") != 1:
        raise ConfigurationError("only scenario schema version 1 is supported")

    suite_raw = raw.get("suite")
    if not isinstance(suite_raw, dict):
        raise ConfigurationError("missing 'suite' mapping")
    broker_raw = raw.get("broker", {})
    if not isinstance(broker_raw, dict):
        raise ConfigurationError("'broker' must be a mapping")

    broker = BrokerConfig(
        host=str(broker_raw.get("host", "127.0.0.1")),
        port=int(broker_raw.get("port", 1883)),
        keepalive=int(broker_raw.get("keepalive", 30)),
    )
    if not 1 <= broker.port <= 65535:
        raise ConfigurationError("broker port must be between 1 and 65535")
    if broker.keepalive <= 0:
        raise ConfigurationError("broker keepalive must be greater than zero")

    scenarios_raw = raw.get("scenarios")
    if not isinstance(scenarios_raw, list) or not scenarios_raw:
        raise ConfigurationError("'scenarios' must be a non-empty list")
    scenarios = tuple(
        _parse_scenario(scenario, index)
        for index, scenario in enumerate(scenarios_raw, start=1)
    )

    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ConfigurationError("scenario IDs must be unique")

    return SuiteConfig(
        name=str(_required(suite_raw, "name", "suite")),
        description=str(suite_raw.get("description", "")),
        broker=broker,
        scenarios=scenarios,
    )
