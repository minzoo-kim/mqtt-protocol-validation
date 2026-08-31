from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

import paho.mqtt.client as mqtt


class MqttOperationError(RuntimeError):
    """Raised when an MQTT network operation does not complete as expected."""


@dataclass(frozen=True)
class ReceivedMessage:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    dup: bool

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8")


EventCallback = Callable[[str, dict[str, Any]], None]


def encode_payload(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


class MqttProbe:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        keepalive: int,
        client_id: str,
        clean_session: bool = True,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.keepalive = keepalive
        self.client_id = client_id
        self.clean_session = clean_session
        self._event_callback = event_callback or (lambda _stage, _details: None)
        self._connected = threading.Event()
        self._disconnected = threading.Event()
        self._subscribed = threading.Event()
        self._messages: queue.Queue[ReceivedMessage] = queue.Queue()
        self._connect_error: str | None = None
        self.session_present = False
        self.last_disconnect_reason: str | None = None
        self._loop_started = False

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=clean_session,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message

    @property
    def is_running(self) -> bool:
        return self._loop_started

    def _emit(self, stage: str, **details: Any) -> None:
        self._event_callback(stage, {"client_id": self.client_id, **details})

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            self._connect_error = str(reason_code)
        else:
            self._connect_error = None
        self.session_present = bool(getattr(flags, "session_present", False))
        self._connected.set()
        self._disconnected.clear()
        self._emit(
            "connected",
            reason_code=str(reason_code),
            clean_session=self.clean_session,
            session_present=self.session_present,
        )

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        self._connected.clear()
        self.last_disconnect_reason = str(reason_code)
        self._disconnected.set()
        self._emit("disconnected", reason_code=self.last_disconnect_reason)

    def _on_subscribe(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        mid: int,
        reason_code_list: list[mqtt.ReasonCode],
        _properties: mqtt.Properties | None,
    ) -> None:
        self._subscribed.set()
        self._emit(
            "subscribed",
            message_id=mid,
            reason_codes=[str(code) for code in reason_code_list],
        )

    def _on_message(
        self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        received = ReceivedMessage(
            topic=message.topic,
            payload=bytes(message.payload),
            qos=message.qos,
            retain=message.retain,
            dup=message.dup,
        )
        self._messages.put(received)
        self._emit(
            "message_received",
            topic=received.topic,
            qos=received.qos,
            retain=received.retain,
            dup=received.dup,
            payload=received.text,
        )

    def connect(self, timeout_s: float) -> None:
        self._connected.clear()
        self._disconnected.clear()
        self._connect_error = None
        self.session_present = False
        rc = self._client.connect(self.host, self.port, self.keepalive)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttOperationError(f"connect returned {mqtt.error_string(rc)}")
        self._client.loop_start()
        self._loop_started = True
        if not self._connected.wait(timeout_s):
            self.disconnect()
            raise MqttOperationError(
                f"connection to {self.host}:{self.port} timed out after {timeout_s}s"
            )
        if self._connect_error:
            connect_error = self._connect_error
            self.disconnect()
            raise MqttOperationError(f"broker rejected connection: {connect_error}")

    def subscribe(self, topic: str, qos: int, timeout_s: float) -> None:
        self._subscribed.clear()
        rc, mid = self._client.subscribe(topic, qos=qos)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttOperationError(f"subscribe returned {mqtt.error_string(rc)}")
        if not self._subscribed.wait(timeout_s):
            raise MqttOperationError(
                f"subscription to {topic} timed out after {timeout_s}s"
            )
        self._emit("subscription_ready", topic=topic, qos=qos, message_id=mid)

    def publish(
        self,
        topic: str,
        payload: Any,
        qos: int,
        *,
        retain: bool = False,
        timeout_s: float,
    ) -> None:
        encoded = encode_payload(payload)
        info = self._client.publish(topic, encoded, qos=qos, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttOperationError(f"publish returned {mqtt.error_string(info.rc)}")
        info.wait_for_publish(timeout=timeout_s)
        if not info.is_published():
            raise MqttOperationError(
                f"publish to {topic} was not acknowledged within {timeout_s}s"
            )
        self._emit(
            "published",
            topic=topic,
            qos=qos,
            retain=retain,
            payload=encoded.decode("utf-8"),
            message_id=info.mid,
        )

    def wait_message(self, timeout_s: float) -> ReceivedMessage | None:
        try:
            return self._messages.get(timeout=timeout_s)
        except queue.Empty:
            self._emit("message_timeout", timeout_s=timeout_s)
            return None

    def prepare_disconnect_observation(self) -> None:
        self._disconnected.clear()
        self.last_disconnect_reason = None

    def wait_disconnected(self, timeout_s: float) -> bool:
        disconnected = self._disconnected.wait(timeout_s)
        if not disconnected:
            self._emit("disconnect_timeout", timeout_s=timeout_s)
        return disconnected

    def disconnect(self) -> None:
        if not self._loop_started:
            return
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()
            self._loop_started = False

    def reconnect(self, timeout_s: float) -> None:
        if self._loop_started:
            self._client.loop_stop()
            self._loop_started = False
        self._connected.clear()
        self._disconnected.clear()
        self._connect_error = None
        self.session_present = False
        rc = self._client.reconnect()
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttOperationError(f"reconnect returned {mqtt.error_string(rc)}")
        self._client.loop_start()
        self._loop_started = True
        if not self._connected.wait(timeout_s):
            self.disconnect()
            raise MqttOperationError(f"reconnect timed out after {timeout_s}s")
        if self._connect_error:
            connect_error = self._connect_error
            self.disconnect()
            raise MqttOperationError(f"broker rejected reconnect: {connect_error}")

    def close(self) -> None:
        try:
            self.disconnect()
        except Exception:
            if self._loop_started:
                self._client.loop_stop()
                self._loop_started = False
