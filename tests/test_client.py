import json
from unittest.mock import Mock

import paho.mqtt.client as mqtt
import pytest

from mqtt_validator.client import MqttOperationError, MqttProbe, encode_payload


def make_probe(events: list[tuple[str, dict[str, object]]] | None = None) -> MqttProbe:
    def record(stage: str, details: dict[str, object]) -> None:
        if events is not None:
            events.append((stage, details))

    return MqttProbe(
        host="127.0.0.1",
        port=1883,
        keepalive=10,
        client_id="unit-probe",
        event_callback=record,
    )


def test_encode_payload_preserves_raw_string_for_malformed_fixture() -> None:
    assert encode_payload('{"broken":').decode("utf-8") == '{"broken":'


def test_encode_payload_serializes_structured_data_as_json() -> None:
    encoded = encode_payload({"message_id": "m-1", "value": 3})

    assert json.loads(encoded) == {"message_id": "m-1", "value": 3}


def test_encode_payload_preserves_bytes_without_copying() -> None:
    payload = b"\x00\xffraw"

    assert encode_payload(payload) is payload


def test_wait_message_timeout_returns_none_and_emits_diagnostic() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    probe = make_probe(events)

    assert probe.wait_message(timeout_s=0) is None
    assert events == [
        ("message_timeout", {"client_id": "unit-probe", "timeout_s": 0})
    ]


def test_disconnect_callback_records_reason_and_clears_connected_state() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    probe = make_probe(events)
    probe._connected.set()

    probe._on_disconnect(
        probe._client,
        None,
        Mock(),
        "Unspecified error",  # type: ignore[arg-type]
        None,
    )

    assert not probe._connected.is_set()
    assert probe.wait_disconnected(timeout_s=0)
    assert probe.last_disconnect_reason == "Unspecified error"
    assert events[-1] == (
        "disconnected",
        {"client_id": "unit-probe", "reason_code": "Unspecified error"},
    )


def test_connect_timeout_stops_network_loop() -> None:
    probe = make_probe()
    probe._client.connect = Mock(return_value=mqtt.MQTT_ERR_SUCCESS)  # type: ignore[method-assign]
    probe._client.disconnect = Mock()  # type: ignore[method-assign]
    probe._client.loop_start = Mock()  # type: ignore[method-assign]
    probe._client.loop_stop = Mock()  # type: ignore[method-assign]

    with pytest.raises(MqttOperationError, match="timed out after 0s"):
        probe.connect(timeout_s=0)

    probe._client.disconnect.assert_called_once_with()
    probe._client.loop_stop.assert_called_once_with()
    assert not probe.is_running


def test_reconnect_timeout_stops_network_loop() -> None:
    probe = make_probe()
    probe._client.reconnect = Mock(return_value=mqtt.MQTT_ERR_SUCCESS)  # type: ignore[method-assign]
    probe._client.disconnect = Mock()  # type: ignore[method-assign]
    probe._client.loop_start = Mock()  # type: ignore[method-assign]
    probe._client.loop_stop = Mock()  # type: ignore[method-assign]

    with pytest.raises(MqttOperationError, match="reconnect timed out after 0s"):
        probe.reconnect(timeout_s=0)

    probe._client.disconnect.assert_called_once_with()
    probe._client.loop_stop.assert_called_once_with()
    assert not probe.is_running


def test_subscribe_timeout_is_reported_with_topic() -> None:
    probe = make_probe()
    probe._client.subscribe = Mock(  # type: ignore[method-assign]
        return_value=(mqtt.MQTT_ERR_SUCCESS, 17)
    )

    with pytest.raises(
        MqttOperationError,
        match=r"subscription to validator/unit timed out after 0s",
    ):
        probe.subscribe("validator/unit", qos=1, timeout_s=0)


def test_publish_without_acknowledgement_is_reported() -> None:
    probe = make_probe()
    publish_info = Mock(rc=mqtt.MQTT_ERR_SUCCESS)
    publish_info.is_published.return_value = False
    probe._client.publish = Mock(return_value=publish_info)  # type: ignore[method-assign]

    with pytest.raises(
        MqttOperationError,
        match=r"publish to validator/unit was not acknowledged within 0s",
    ):
        probe.publish(
            "validator/unit",
            {"value": 1},
            qos=1,
            timeout_s=0,
        )

    publish_info.wait_for_publish.assert_called_once_with(timeout=0)
