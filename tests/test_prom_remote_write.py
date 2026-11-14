"""Contract tests for historical Prometheus remote-write payloads.

No Prometheus, Mimir or HAOS server is needed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import struct

from interval_archive import IntervalRecord
from prom_remote_write import _snappy_literals, encode_write_request, records_to_series


def _read_varint(data: bytes, pos: int = 0):
    value = shift = 0
    while True:
        b = data[pos]
        pos += 1
        value |= (b & 127) << shift
        if b < 128:
            return value, pos
        shift += 7


def _decode_snappy_literal_only(data: bytes) -> bytes:
    expected, pos = _read_varint(data)
    output = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        assert tag & 3 == 0
        size_code = tag >> 2
        if size_code < 60:
            size = size_code + 1
        else:
            width = size_code - 59
            size = int.from_bytes(data[pos:pos + width], "little") + 1
            pos += width
        output.extend(data[pos:pos + size])
        pos += size
    assert len(output) == expected
    return bytes(output)


def _record(kind, time, energy):
    return IntervalRecord(start=time, kind=kind, kwh=energy)


def test_remote_write_uses_original_30_min_timestamp_and_real_watts():
    t = datetime(2026, 11, 12, 1, 30, tzinfo=UTC)
    groups, pending, conflicts = records_to_series(
        "contract-test", [_record("import", t, 0.4)],
        [_record("export", t, 0.7)],
    )
    assert not conflicts
    assert pending[f"haggle_grid_import_average_w/{int(t.timestamp()*1000)}"] == 800
    assert pending[f"haggle_grid_export_average_w/{int(t.timestamp()*1000)}"] == 1400
    assert pending[f"haggle_grid_net_average_w/{int(t.timestamp()*1000)}"] == -600
    assert all(group[1] == ("contract", "contract-test") for group in groups)
    raw = _decode_snappy_literal_only(encode_write_request(groups))
    assert b"haggle_grid_net_average_w" in raw
    assert struct.pack("<d", -600.0) in raw


def test_missing_export_does_not_invent_zero_export_or_net():
    t = datetime(2026, 11, 12, 1, 30, tzinfo=UTC)
    groups, pending, _ = records_to_series(
        "contract-test", [_record("import", t, 0.4)], []
    )
    assert not any("net" in key or "export" in key for key in pending)


def test_confirmed_samples_are_idempotent_and_corrections_are_flagged():
    t = datetime(2026, 11, 12, 1, 30, tzinfo=UTC)
    _, accepted, _ = records_to_series("contract-test", [_record("import", t, 0.4)], [])
    groups, pending, conflicts = records_to_series(
        "contract-test", [_record("import", t, 0.5)], [], sent=accepted
    )
    assert conflicts
    assert not pending
    assert groups == {}


def test_writer_has_valid_empty_snappy_envelope():
    assert _decode_snappy_literal_only(encode_write_request({})) == b""


def test_utc_equivalent_times_address_same_series():
    t = datetime(2026, 11, 12, 1, 30, tzinfo=UTC)
    local = t.astimezone(UTC + timedelta(hours=10))
    _, a, _ = records_to_series("contract-test", [_record("import", t, 0.4)], [])
    _, b, _ = records_to_series("contract-test", [_record("import", local, 0.4)], [])
    assert a == b
