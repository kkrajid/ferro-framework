"""The JSON codec lives in Rust; these tests exercise it through the native module."""

import datetime as dt
import json
import uuid
from decimal import Decimal

import pytest

from ferro._ferro import json_dumps, json_loads


def test_basic_values():
    assert json_dumps({"a": [1, 2.5, True, False, None, "x"]}) == (
        b'{"a":[1,2.5,true,false,null,"x"]}'
    )
    assert json_dumps((1, 2)) == b"[1,2]"
    assert json_dumps([]) == b"[]"
    assert json_dumps({}) == b"{}"


def test_floats_round_trip():
    for value in [1.0, 0.1, -2.5e-8, 1e300, 123456789.125]:
        assert json.loads(json_dumps(value)) == value


def test_big_integers_are_exact():
    big = 2**80 + 1
    assert json_dumps(big) == str(big).encode()
    assert json_dumps(-big) == str(-big).encode()
    assert json_loads(str(big).encode()) == big
    assert json_loads(b"-9223372036854775809") == -9223372036854775809


def test_strings_are_escaped_and_unicode_preserved():
    assert json_dumps('"\n\\') == b'"\\"\\n\\\\"'
    text = "മലയാളം"
    assert json_dumps(text) == f'"{text}"'.encode()
    assert json_loads(f'"{text}"'.encode()) == text


def test_non_string_keys_follow_python_json():
    assert json_dumps({1: "a"}) == b'{"1":"a"}'
    assert json_dumps({True: "b"}) == b'{"true":"b"}'
    assert json_dumps({None: "c"}) == b'{"null":"c"}'
    assert json_dumps({1.5: "d"}) == b'{"1.5":"d"}'
    with pytest.raises(TypeError, match="tuple"):
        json_dumps({(1, 2): "x"})


def test_special_types():
    when = dt.datetime(2026, 10, 10, 12, 30, 5, tzinfo=dt.UTC)
    ident = uuid.UUID("12345678-1234-5678-1234-567812345678")
    encoded = json.loads(
        json_dumps(
            {
                "datetime": when,
                "date": dt.date(2026, 10, 10),
                "time": dt.time(8, 15),
                "decimal": Decimal("1.10"),
                "uuid": ident,
            }
        )
    )
    assert encoded == {
        "datetime": "2026-10-10T12:30:05+00:00",
        "date": "2026-10-10",
        "time": "08:15:00",
        "decimal": "1.10",
        "uuid": "12345678-1234-5678-1234-567812345678",
    }


def test_rejects_nan_and_infinity():
    for value in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValueError, match=r"NaN|Infinity"):
            json_dumps({"x": value})


def test_rejects_unsupported_types_naming_them():
    with pytest.raises(TypeError, match="set"):
        json_dumps({"x": {1, 2}})

    class Custom:
        pass

    with pytest.raises(TypeError, match="Custom"):
        json_dumps([Custom()])


def test_depth_limit():
    value: list = []
    for _ in range(300):
        value = [value]
    with pytest.raises(ValueError, match="deep"):
        json_dumps(value)


def test_loads_preserves_key_order_and_types():
    value = json_loads(b'{"z": 1, "a": [true, null, 1.5, "s"], "m": {"k": -3}}')
    assert list(value) == ["z", "a", "m"]
    assert value == {"z": 1, "a": [True, None, 1.5, "s"], "m": {"k": -3}}
    assert isinstance(value["z"], int)


def test_loads_rejects_invalid_json():
    with pytest.raises(ValueError):
        json_loads(b"{bad")
    with pytest.raises(ValueError):
        json_loads(b"")
