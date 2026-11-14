"""Prometheus remote-write v1 for historical AGL intervals.

Pure-Python protobuf and Snappy block encoding: no additional Python packages.
This is a *push* endpoint, not HA's scrape exporter. The receiving Prometheus/
Mimir must explicitly support the age and ordering of delayed historical data.
"""
from __future__ import annotations

import math
import struct
from datetime import UTC, datetime, timedelta
from typing import Iterable


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def _field_bytes(number: int, data: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(data)) + data


def _label(name: str, value: str) -> bytes:
    return _field_bytes(1, name.encode()) + _field_bytes(2, value.encode())


def _sample(value: float, timestamp_ms: int) -> bytes:
    if not math.isfinite(value):
        raise ValueError("remote-write samples must be finite")
    if timestamp_ms < 0:
        raise ValueError("negative timestamps not supported")
    return bytes((9,)) + struct.pack("<d", value) + bytes((16,)) + _varint(timestamp_ms)


def _snappy_literals(data: bytes) -> bytes:
    """Valid Snappy raw block with literal runs (no external snappy dependency)."""
    out = bytearray(_varint(len(data)))
    offset = 0
    while offset < len(data):
        part = data[offset : offset + 65536]
        n = len(part) - 1
        if n < 60:
            out.append(n << 2)
        else:
            width = max(1, (n.bit_length() + 7) // 8)
            out.append((59 + width) << 2)
            out.extend(n.to_bytes(width, "little"))
        out.extend(part)
        offset += len(part)
    return bytes(out)


def encode_write_request(series: dict[tuple[tuple[str, str], ...], list[tuple[int, float]]]) -> bytes:
    """Encode TimeSeries[] as WriteRequest field 1, Snappy raw compressed."""
    request = bytearray()
    for labels, samples in sorted(series.items()):
        body = bytearray()
        if "__name__" not in dict(labels):
            raise ValueError("a metric name label is mandatory")
        for name, value in sorted(labels):
            body.extend(_field_bytes(1, _label(name, value)))
        for timestamp, value in sorted(samples):
            body.extend(_field_bytes(2, _sample(value, timestamp)))
        request.extend(_field_bytes(1, body))
    return _snappy_literals(bytes(request))


def records_since(records: Iterable, *, now: datetime, max_age: timedelta) -> list:
    """Keep only records recent enough for the receiver's delayed-write window."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if max_age < timedelta(0):
        raise ValueError("max_age must be non-negative")
    cutoff = now.astimezone(UTC) - max_age
    return [record for record in records if record.start.astimezone(UTC) >= cutoff]


def records_to_series(
    contract: str,
    imports: Iterable,
    exports: Iterable,
    *,
    sent: dict[str, float] | None = None,
) -> tuple[dict, dict[str, float], list[str]]:
    """Build historic samples and a pending write ledger.

    Already confirmed samples are omitted. Corrections to previously accepted
    samples are reported, *not* resent: ordinary TSDBs reject duplicate
    timestamp/different-value writes. Callers must handle such corrections
    through an explicit replacement/backfill procedure.
    """
    sent = sent or {}
    import_rows = {r.start.astimezone(UTC): r for r in imports}
    export_rows = {r.start.astimezone(UTC): r for r in exports}
    candidate = {}
    conflicts = []

    def add(metric: str, stamp: datetime, value: float) -> None:
        timestamp = int(stamp.timestamp() * 1000)
        key = f"{metric}/{timestamp}"
        if key in sent:
            if not math.isclose(sent[key], value, rel_tol=0, abs_tol=1e-10):
                conflicts.append(key)
            return
        candidate[key] = value

    for kind, rows in (("import", import_rows), ("export", export_rows)):
        for stamp, record in rows.items():
            add(f"haggle_grid_{kind}_interval_kwh", stamp, record.kwh)
            add(f"haggle_grid_{kind}_average_w", stamp, record.kwh * 2000)

    for stamp in sorted(import_rows.keys() & export_rows.keys()):
        add(
            "haggle_grid_net_average_w",
            stamp,
            round((import_rows[stamp].kwh - export_rows[stamp].kwh) * 2000, 10),
        )

    groups = {}
    for key, value in candidate.items():
        metric, timestamp = key.rsplit("/", 1)
        labels = (("__name__", metric), ("contract", contract), ("source", "haggle"))
        groups.setdefault(labels, []).append((int(timestamp), value))
    return groups, candidate, sorted(conflicts)
