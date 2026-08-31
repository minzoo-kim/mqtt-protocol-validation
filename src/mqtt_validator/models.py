from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


VALID_SCENARIO_KINDS = {
    "publish_receive",
    "retained",
    "duplicate_detection",
    "malformed_payload",
    "expected_timeout",
    "reconnect",
    "topic_isolation",
}


@dataclass(frozen=True)
class BrokerConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    keepalive: int = 30


@dataclass(frozen=True)
class Scenario:
    id: str
    requirement_id: str
    name: str
    kind: str
    topic: str
    qos: int = 0
    subscribe_qos: int = 0
    timeout_s: float = 2.0
    payload: Any = None
    expected: dict[str, Any] = field(default_factory=dict)
    publish_topic: str | None = None


@dataclass(frozen=True)
class SuiteConfig:
    name: str
    description: str
    broker: BrokerConfig
    scenarios: tuple[Scenario, ...]


@dataclass(frozen=True)
class EventRecord:
    timestamp: str
    case_id: str
    requirement_id: str
    stage: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioResult:
    case_id: str
    requirement_id: str
    name: str
    status: str
    started_at: str
    duration_ms: int
    expected: dict[str, Any]
    observed: dict[str, Any]
    mismatches: list[str] = field(default_factory=list)
    error: str | None = None
    events: list[EventRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["events"] = [event.to_dict() for event in self.events]
        return result


@dataclass
class RunReport:
    run_id: str
    suite_name: str
    suite_description: str
    started_at: str
    finished_at: str
    broker: BrokerConfig
    results: list[ScenarioResult]

    @property
    def passed(self) -> int:
        return sum(result.status == "passed" for result in self.results)

    @property
    def failed(self) -> int:
        return sum(result.status == "failed" for result in self.results)

    @property
    def success(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "suite": {
                "name": self.suite_name,
                "description": self.suite_description,
            },
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "broker": asdict(self.broker),
            "summary": {
                "total": len(self.results),
                "passed": self.passed,
                "failed": self.failed,
                "success": self.success,
            },
            "results": [result.to_dict() for result in self.results],
        }
