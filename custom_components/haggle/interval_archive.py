"""Small durable-format model for Haggle's original AGL intervals.

The archive deliberately has no Home Assistant imports.  The coordinator can
feed it the parsed readings before hourly aggregation, while a HA ``Store``
adapter can persist the JSON returned by :meth:`to_json` without coupling the
data model to Recorder's hourly statistics schema.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
import math


@dataclass(frozen=True, slots=True)
class IntervalRecord:
    """One corrected, timestamped AGL interval."""

    start: datetime
    kind: str
    kwh: float
    cost_aud: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON representation."""
        data = asdict(self)
        data["start"] = self.start.astimezone(UTC).isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntervalRecord:
        """Restore a record from the persisted representation."""
        return cls(
            start=datetime.fromisoformat(str(data["start"])).astimezone(UTC),
            kind=str(data["kind"]),
            kwh=float(data["kwh"]),
            cost_aud=(
                None if data.get("cost_aud") is None else float(data["cost_aud"])
            ),
        )


class IntervalArchive:
    """In-memory interval index with deterministic JSON persistence.

    The key is ``(contract, kind, start)``.  A later AGL poll therefore replaces
    a corrected interval instead of appending a duplicate.  The integration's
    HA storage adapter can persist the JSON between restarts.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, datetime], IntervalRecord] = {}

    def upsert(self, contract: str, records: list[IntervalRecord]) -> None:
        """Insert or replace records by contract, kind, and timestamp."""
        for record in records:
            if record.kind not in {"import", "export"}:
                raise ValueError(f"unsupported interval kind: {record.kind}")
            if record.start.tzinfo is None:
                raise ValueError("interval timestamps must be timezone-aware")
            if not math.isfinite(record.kwh) or record.kwh < 0:
                raise ValueError("interval kWh must be finite and non-negative")
            if record.cost_aud is not None and not math.isfinite(record.cost_aud):
                raise ValueError("interval cost must be finite")
            start = record.start.astimezone(UTC)
            if start.minute not in (0, 30) or start.second or start.microsecond:
                raise ValueError("interval timestamps must be on half-hour boundaries")
            canonical = IntervalRecord(
                start=start, kind=record.kind, kwh=record.kwh, cost_aud=record.cost_aud
            )
            self._records[(contract, record.kind, start)] = canonical

    def records(self, contract: str, kind: str) -> list[IntervalRecord]:
        """Return all records for a contract/kind in timestamp order."""
        return sorted(
            (
                record
                for (stored_contract, stored_kind, _), record in self._records.items()
                if stored_contract == contract and stored_kind == kind
            ),
            key=lambda record: record.start,
        )

    def query(
        self, contract: str, kind: str, start: datetime, end: datetime
    ) -> list[IntervalRecord]:
        """Return half-open [start, end) records, in original UTC slots."""
        if kind not in {"import", "export"}:
            raise ValueError("invalid kind")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("query bounds must have timezones")
        low, high = start.astimezone(UTC), end.astimezone(UTC)
        if low >= high:
            raise ValueError("start must precede end")
        return [r for r in self.records(contract, kind) if low <= r.start < high]

    def to_json(self) -> str:
        """Serialize the archive for HA's persistent storage helper."""
        payload = [
            {"contract": contract, **record.as_dict()}
            for (contract, _, _), record in sorted(
                self._records.items(), key=lambda item: item[0]
            )
        ]
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> IntervalArchive:
        """Restore an archive created by :meth:`to_json`."""
        archive = cls()
        for raw in json.loads(payload):
            record = IntervalRecord.from_dict(raw)
            archive.upsert(str(raw["contract"]), [record])
        return archive
