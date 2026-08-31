from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from mqtt_validator.client import MqttProbe, ReceivedMessage
from mqtt_validator.models import (
    BrokerConfig,
    EventRecord,
    RunReport,
    Scenario,
    ScenarioResult,
    SuiteConfig,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def interpolate(value: Any, run_id: str) -> Any:
    if isinstance(value, str):
        return value.replace("{run_id}", run_id)
    if isinstance(value, list):
        return [interpolate(item, run_id) for item in value]
    if isinstance(value, dict):
        return {key: interpolate(item, run_id) for key, item in value.items()}
    return value


def compare_expected(
    expected: dict[str, Any], observed: dict[str, Any]
) -> list[str]:
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        if key not in observed:
            mismatches.append(f"{key}: expected {expected_value!r}, but no value was observed")
            continue
        observed_value = observed[key]
        if observed_value != expected_value:
            mismatches.append(
                f"{key}: expected {expected_value!r}, observed {observed_value!r}"
            )
    return mismatches


class SuiteRunner:
    def __init__(self, suite: SuiteConfig) -> None:
        self.suite = suite
        self.run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        self._case_events: list[EventRecord] = []
        self._active_scenario: Scenario | None = None
        self._probe_counter = 0
        self._executors: dict[str, Callable[[Scenario], dict[str, Any]]] = {
            "publish_receive": self._publish_receive,
            "retained": self._retained,
            "duplicate_detection": self._duplicate_detection,
            "malformed_payload": self._malformed_payload,
            "expected_timeout": self._expected_timeout,
            "reconnect": self._reconnect,
            "topic_isolation": self._topic_isolation,
        }

    def _emit(self, stage: str, details: dict[str, Any] | None = None) -> None:
        if self._active_scenario is None:
            return
        self._case_events.append(
            EventRecord(
                timestamp=utc_now(),
                case_id=self._active_scenario.id,
                requirement_id=self._active_scenario.requirement_id,
                stage=stage,
                details=details or {},
            )
        )

    def _probe(self, role: str) -> MqttProbe:
        self._probe_counter += 1
        compact_run_id = self.run_id[-8:]
        client_id = f"val-{compact_run_id}-{self._probe_counter}-{role[0]}"
        broker: BrokerConfig = self.suite.broker
        return MqttProbe(
            host=broker.host,
            port=broker.port,
            keepalive=broker.keepalive,
            client_id=client_id,
            event_callback=self._emit,
        )

    @staticmethod
    def _decoded_payload(message: ReceivedMessage, source_payload: Any) -> Any:
        if isinstance(source_payload, (dict, list, int, float, bool)) or source_payload is None:
            return json.loads(message.text)
        return message.text

    def _publish_receive(self, scenario: Scenario) -> dict[str, Any]:
        subscriber = self._probe("subscriber")
        publisher = self._probe("publisher")
        try:
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            publisher.connect(scenario.timeout_s)
            publisher.publish(
                scenario.publish_topic or scenario.topic,
                scenario.payload,
                scenario.qos,
                timeout_s=scenario.timeout_s,
            )
            message = subscriber.wait_message(scenario.timeout_s)
            if message is None:
                return {
                    "received_count": 0,
                    "timed_out": True,
                    "payload_matches": False,
                }
            decoded = self._decoded_payload(message, scenario.payload)
            return {
                "received_count": 1,
                "delivered_qos": message.qos,
                "retain": message.retain,
                "dup": message.dup,
                "topic": message.topic,
                "payload": decoded,
                "payload_matches": decoded == scenario.payload,
            }
        finally:
            publisher.close()
            subscriber.close()

    def _retained(self, scenario: Scenario) -> dict[str, Any]:
        publisher = self._probe("publisher")
        subscriber = self._probe("subscriber")
        try:
            publisher.connect(scenario.timeout_s)
            publisher.publish(
                scenario.topic,
                scenario.payload,
                scenario.qos,
                retain=True,
                timeout_s=scenario.timeout_s,
            )
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            message = subscriber.wait_message(scenario.timeout_s)
            if message is None:
                return {
                    "received_count": 0,
                    "timed_out": True,
                    "retain": False,
                    "payload_matches": False,
                }
            decoded = self._decoded_payload(message, scenario.payload)
            return {
                "received_count": 1,
                "delivered_qos": message.qos,
                "retain": message.retain,
                "payload": decoded,
                "payload_matches": decoded == scenario.payload,
            }
        finally:
            if publisher.is_running:  # keep cleanup deterministic after partial failure
                try:
                    publisher.publish(
                        scenario.topic,
                        b"",
                        scenario.qos,
                        retain=True,
                        timeout_s=scenario.timeout_s,
                    )
                    self._emit("retained_state_cleared", {"topic": scenario.topic})
                except Exception as exc:
                    self._emit("cleanup_warning", {"error": str(exc)})
            subscriber.close()
            publisher.close()

    def _duplicate_detection(self, scenario: Scenario) -> dict[str, Any]:
        subscriber = self._probe("subscriber")
        publisher = self._probe("publisher")
        try:
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            publisher.connect(scenario.timeout_s)
            for occurrence in (1, 2):
                publisher.publish(
                    scenario.topic,
                    scenario.payload,
                    scenario.qos,
                    timeout_s=scenario.timeout_s,
                )
                self._emit("duplicate_fixture_published", {"occurrence": occurrence})

            messages = [subscriber.wait_message(scenario.timeout_s) for _ in range(2)]
            received = [message for message in messages if message is not None]
            message_ids: list[str] = []
            parse_errors: list[str] = []
            for message in received:
                try:
                    decoded = json.loads(message.text)
                    message_ids.append(str(decoded["message_id"]))
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    parse_errors.append(str(exc))
            unique_count = len(set(message_ids))
            return {
                "raw_count": len(received),
                "unique_count": unique_count,
                "duplicate_count": len(message_ids) - unique_count,
                "message_ids": message_ids,
                "parse_errors": parse_errors,
            }
        finally:
            publisher.close()
            subscriber.close()

    def _malformed_payload(self, scenario: Scenario) -> dict[str, Any]:
        subscriber = self._probe("subscriber")
        publisher = self._probe("publisher")
        try:
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            publisher.connect(scenario.timeout_s)
            publisher.publish(
                scenario.topic,
                scenario.payload,
                scenario.qos,
                timeout_s=scenario.timeout_s,
            )
            message = subscriber.wait_message(scenario.timeout_s)
            if message is None:
                return {"rejected": False, "timed_out": True}
            try:
                json.loads(message.text)
            except json.JSONDecodeError as exc:
                self._emit(
                    "payload_rejected",
                    {
                        "error_type": type(exc).__name__,
                        "reason": exc.msg,
                        "line": exc.lineno,
                        "column": exc.colno,
                    },
                )
                return {
                    "rejected": True,
                    "error_type": type(exc).__name__,
                    "reason": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                }
            return {"rejected": False, "error_type": None}
        finally:
            publisher.close()
            subscriber.close()

    def _expected_timeout(self, scenario: Scenario) -> dict[str, Any]:
        subscriber = self._probe("subscriber")
        try:
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            message = subscriber.wait_message(scenario.timeout_s)
            return {
                "received_count": 0 if message is None else 1,
                "timed_out": message is None,
            }
        finally:
            subscriber.close()

    def _reconnect(self, scenario: Scenario) -> dict[str, Any]:
        subscriber = self._probe("subscriber")
        publisher = self._probe("publisher")
        try:
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            subscriber.disconnect()
            self._emit("reconnect_attempt", {"attempt": 1})
            subscriber.reconnect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            publisher.connect(scenario.timeout_s)
            publisher.publish(
                scenario.topic,
                scenario.payload,
                scenario.qos,
                timeout_s=scenario.timeout_s,
            )
            message = subscriber.wait_message(scenario.timeout_s)
            if message is None:
                return {
                    "received_count": 0,
                    "reconnect_count": 1,
                    "payload_matches": False,
                }
            decoded = self._decoded_payload(message, scenario.payload)
            return {
                "received_count": 1,
                "reconnect_count": 1,
                "delivered_qos": message.qos,
                "payload": decoded,
                "payload_matches": decoded == scenario.payload,
            }
        finally:
            publisher.close()
            subscriber.close()

    def _topic_isolation(self, scenario: Scenario) -> dict[str, Any]:
        subscriber = self._probe("subscriber")
        publisher = self._probe("publisher")
        try:
            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            publisher.connect(scenario.timeout_s)
            publisher.publish(
                scenario.publish_topic or f"{scenario.topic}/other",
                scenario.payload,
                scenario.qos,
                timeout_s=scenario.timeout_s,
            )
            message = subscriber.wait_message(scenario.timeout_s)
            return {
                "received_count": 0 if message is None else 1,
                "timed_out": message is None,
            }
        finally:
            publisher.close()
            subscriber.close()

    def _runtime_scenario(self, scenario: Scenario) -> Scenario:
        return replace(
            scenario,
            topic=interpolate(scenario.topic, self.run_id),
            publish_topic=(
                interpolate(scenario.publish_topic, self.run_id)
                if scenario.publish_topic
                else None
            ),
            payload=interpolate(scenario.payload, self.run_id),
            expected=interpolate(scenario.expected, self.run_id),
        )

    def _run_case(self, configured: Scenario) -> ScenarioResult:
        scenario = self._runtime_scenario(configured)
        self._active_scenario = scenario
        self._case_events = []
        started_at = utc_now()
        started_clock = time.perf_counter()
        observed: dict[str, Any] = {}
        error: str | None = None
        self._emit("case_started", {"name": scenario.name, "kind": scenario.kind})
        try:
            observed = self._executors[scenario.kind](scenario)
            mismatches = compare_expected(scenario.expected, observed)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            mismatches = []
            self._emit("case_exception", {"error": error})
        status = "passed" if not error and not mismatches else "failed"
        duration_ms = round((time.perf_counter() - started_clock) * 1000)
        self._emit(
            "case_finished",
            {"status": status, "duration_ms": duration_ms, "mismatches": mismatches},
        )
        result = ScenarioResult(
            case_id=scenario.id,
            requirement_id=scenario.requirement_id,
            name=scenario.name,
            status=status,
            started_at=started_at,
            duration_ms=duration_ms,
            expected=scenario.expected,
            observed=observed,
            mismatches=mismatches,
            error=error,
            events=list(self._case_events),
        )
        self._active_scenario = None
        return result

    def run(self) -> RunReport:
        started_at = utc_now()
        results = [self._run_case(scenario) for scenario in self.suite.scenarios]
        return RunReport(
            run_id=self.run_id,
            suite_name=self.suite.name,
            suite_description=self.suite.description,
            started_at=started_at,
            finished_at=utc_now(),
            broker=self.suite.broker,
            results=results,
        )
