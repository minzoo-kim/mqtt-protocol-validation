import json
from unittest.mock import patch

from mqtt_validator.faults import ToxiproxyController


class FakeResponse:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode("utf-8")


@patch("mqtt_validator.faults.urlopen")
def test_toxiproxy_controller_lifecycle(mock_urlopen: object) -> None:
    responses = [
        FakeResponse({"version": "2.12.0"}),
        FakeResponse(None),
        FakeResponse({"name": "mqtt", "enabled": True}),
        FakeResponse({"name": "mqtt", "enabled": False}),
        FakeResponse(None),
    ]
    mock_urlopen.side_effect = responses  # type: ignore[attr-defined]
    controller = ToxiproxyController("http://127.0.0.1:8474")

    assert controller.wait_ready(1.0) == "2.12.0"
    controller.reset()
    assert controller.create_proxy(
        name="mqtt", listen="0.0.0.0:1884", upstream="mosquitto:1883"
    )["enabled"] is True
    assert controller.set_enabled("mqtt", False)["enabled"] is False
    controller.delete_proxy("mqtt")

    methods = [
        call.args[0].method for call in mock_urlopen.call_args_list  # type: ignore[attr-defined]
    ]
    assert methods == ["GET", "POST", "POST", "PATCH", "DELETE"]
