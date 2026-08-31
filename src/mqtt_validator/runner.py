from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from mqtt_validator.client import MqttOperationError, MqttProbe, ReceivedMessage
from mqtt_validator.faults import ToxiproxyController
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
            "connection_cut_recovery": self._connection_cut_recovery,
            "publish_receive": self._publish_receive,
            "retained": self._retained,
            "duplicate_detection": self._duplicate_detection,
            "malformed_payload": self._malformed_payload,
            "expected_timeout": self._expected_timeout,
            "persistent_session": self._persistent_session,
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

    def _probe(
        self,
        role: str,
        *,
        host: str | None = None,
        port: int | None = None,
        client_id: str | None = None,
        clean_session: bool = True,
    ) -> MqttProbe:
        self._probe_counter += 1
        compact_run_id = self.run_id[-8:]
        resolved_client_id = (
            client_id or f"val-{compact_run_id}-{self._probe_counter}-{role[0]}"
        )
        broker: BrokerConfig = self.suite.broker
        return MqttProbe(
            host=host or broker.host,
            port=port or broker.port,
            keepalive=broker.keepalive,
            client_id=resolved_client_id,
            clean_session=clean_session,
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

    def _persistent_session(self, scenario: Scenario) -> dict[str, Any]:
        client_id = f"persist-{self.run_id[-12:]}"
        subscriber = self._probe(
            "persistent-subscriber",
            client_id=client_id,
            clean_session=False,
        )
        publisher = self._probe("publisher")
        try:
            subscriber.connect(scenario.timeout_s)
            first_session_present = subscriber.session_present
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            subscriber.disconnect()
            self._emit(
                "persistent_subscriber_offline",
                {"client_id": client_id, "topic": scenario.topic},
            )

            publisher.connect(scenario.timeout_s)
            publisher.publish(
                scenario.topic,
                scenario.payload,
                scenario.qos,
                timeout_s=scenario.timeout_s,
            )
            self._emit(
                "message_published_while_offline",
                {"topic": scenario.topic, "qos": scenario.qos},
            )

            subscriber.reconnect(scenario.timeout_s)
            resumed_session_present = subscriber.session_present
            message = subscriber.wait_message(scenario.timeout_s)
            if message is None:
                return {
                    "received_count": 0,
                    "first_session_present": first_session_present,
                    "session_present": resumed_session_present,
                    "resubscribed": False,
                    "queued_while_offline": False,
                    "payload_matches": False,
                }
            decoded = self._decoded_payload(message, scenario.payload)
            return {
                "received_count": 1,
                "first_session_present": first_session_present,
                "session_present": resumed_session_present,
                "resubscribed": False,
                "queued_while_offline": True,
                "delivered_qos": message.qos,
                "payload": decoded,
                "payload_matches": decoded == scenario.payload,
            }
        finally:
            publisher.close()
            subscriber.close()
            cleanup = self._probe(
                "session-cleanup",
                client_id=client_id,
                clean_session=True,
            )
            try:
                cleanup.connect(scenario.timeout_s)
                self._emit("persistent_session_cleared", {"client_id": client_id})
            except Exception as exc:
                self._emit("cleanup_warning", {"error": str(exc)})
            finally:
                cleanup.close()

    def _reconnect_with_retry(
        self, probe: MqttProbe, timeout_s: float
    ) -> int:
        deadline = time.monotonic() + timeout_s
        attempts = 0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            attempts += 1
            self._emit("reconnect_attempt", {"attempt": attempts})
            try:
                remaining = max(0.1, deadline - time.monotonic())
                probe.reconnect(min(remaining, 1.0))
                return attempts
            except (MqttOperationError, OSError) as exc:
                last_error = exc
                time.sleep(0.1)
        raise MqttOperationError(
            f"reconnect did not succeed within {timeout_s}s: {last_error}"
        )

    @staticmethod
    def _proxy_options(scenario: Scenario) -> dict[str, Any]:
        proxy = scenario.options.get("proxy")
        if not isinstance(proxy, dict):
            raise ValueError(f"{scenario.id}: options.proxy must be a mapping")
        required = ("api_url", "name", "listen", "upstream", "host", "port")
        missing = [key for key in required if key not in proxy]
        if missing:
            raise ValueError(
                f"{scenario.id}: options.proxy is missing {', '.join(missing)}"
            )
        port = proxy["port"]
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"{scenario.id}: options.proxy.port is invalid")
        return proxy

    def _connection_cut_recovery(self, scenario: Scenario) -> dict[str, Any]:
        proxy = self._proxy_options(scenario)
        controller = ToxiproxyController(str(proxy["api_url"]))
        proxy_name = str(proxy["name"])
        subscriber = self._probe(
            "faulted-subscriber",
            host=str(proxy["host"]),
            port=int(proxy["port"]),
        )
        publisher = self._probe("publisher")
        proxy_created = False
        proxy_enabled = False
        try:
            version = controller.wait_ready(scenario.timeout_s)
            controller.reset()
            controller.create_proxy(
                name=proxy_name,
                listen=str(proxy["listen"]),
                upstream=str(proxy["upstream"]),
            )
            proxy_created = True
            proxy_enabled = True
            self._emit(
                "fault_proxy_ready",
                {"proxy": proxy_name, "toxiproxy_version": version},
            )

            subscriber.connect(scenario.timeout_s)
            subscriber.subscribe(
                scenario.topic, scenario.subscribe_qos, scenario.timeout_s
            )
            subscriber.prepare_disconnect_observation()
            controller.set_enabled(proxy_name, False)
            proxy_enabled = False
            self._emit(
                "connection_cut_injected",
                {"proxy": proxy_name, "mode": "proxy_disabled"},
            )
            cut_detected = subscriber.wait_disconnected(scenario.timeout_s)

            controller.set_enabled(proxy_name, True)
            proxy_enabled = True
            self._emit("connection_restored", {"proxy": proxy_name})
            reconnect_attempts = self._reconnect_with_retry(
                subscriber, scenario.timeout_s
            )
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
                    "connection_cut_detected": cut_detected,
                    "received_count": 0,
                    "reconnect_count": 1,
                    "reconnect_attempts": reconnect_attempts,
                    "payload_matches": False,
                }
            decoded = self._decoded_payload(message, scenario.payload)
            return {
                "connection_cut_detected": cut_detected,
                "disconnect_reason": subscriber.last_disconnect_reason,
                "received_count": 1,
                "reconnect_count": 1,
                "reconnect_attempts": reconnect_attempts,
                "delivered_qos": message.qos,
                "payload": decoded,
                "payload_matches": decoded == scenario.payload,
            }
        finally:
            if proxy_created and not proxy_enabled:
                try:
                    controller.set_enabled(proxy_name, True)
                except Exception as exc:
                    self._emit("cleanup_warning", {"error": str(exc)})
            publisher.close()
            subscriber.close()
            if proxy_created:
                try:
                    controller.delete_proxy(proxy_name)
                    self._emit("fault_proxy_deleted", {"proxy": proxy_name})
                except Exception as exc:
                    self._emit("cleanup_warning", {"error": str(exc)})

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
            options=interpolate(scenario.options, self.run_id),
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
