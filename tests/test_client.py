import json

from mqtt_validator.client import encode_payload


def test_encode_payload_preserves_raw_string_for_malformed_fixture() -> None:
    assert encode_payload('{"broken":').decode("utf-8") == '{"broken":'


def test_encode_payload_serializes_structured_data_as_json() -> None:
    encoded = encode_payload({"message_id": "m-1", "value": 3})

    assert json.loads(encoded) == {"message_id": "m-1", "value": 3}
