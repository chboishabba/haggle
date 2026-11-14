from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from interval_archive import (
    IntervalArchive,
    IntervalRecord,
)


def _record(when: str, kwh: float, kind: str = "import") -> IntervalRecord:
    return IntervalRecord(
        start=datetime.fromisoformat(when).replace(tzinfo=UTC),
        kind=kind,
        kwh=kwh,
        cost_aud=round(kwh * 0.3, 2),
    )


def test_upsert_replaces_corrected_interval_by_kind_and_timestamp() -> None:
    archive = IntervalArchive()
    archive.upsert(
        "999",
        [_record("2026-11-01T00:00:00", 0.5), _record("2026-11-01T00:30:00", 0.7)],
    )

    archive.upsert("999", [_record("2026-11-01T00:30:00", 0.9)])

    assert archive.records("999", "import") == [
        _record("2026-11-01T00:00:00", 0.5),
        _record("2026-11-01T00:30:00", 0.9),
    ]


def test_import_and_export_records_are_kept_separate() -> None:
    archive = IntervalArchive()
    archive.upsert("999", [_record("2026-11-01T00:00:00", 0.5, "import")])
    archive.upsert("999", [_record("2026-11-01T00:00:00", 0.2, "export")])

    assert archive.records("999", "import")[0].kwh == 0.5
    assert archive.records("999", "export")[0].kwh == 0.2


def test_archive_round_trips_restart_state() -> None:
    archive = IntervalArchive()
    archive.upsert("999", [_record("2026-11-01T00:00:00", 0.5)])

    restored = IntervalArchive.from_json(archive.to_json())

    assert restored.records("999", "import") == archive.records("999", "import")


def test_equivalent_offsets_replace_same_utc_slot() -> None:
    archive = IntervalArchive()
    utc = IntervalRecord(datetime(2026, 11, 1, 0, 30, tzinfo=UTC), "import", 0.2)
    aest = IntervalRecord(
        datetime(2026, 11, 1, 10, 30, tzinfo=timezone(timedelta(hours=10))),
        "import",
        0.4,
    )
    archive.upsert("999", [utc])
    archive.upsert("999", [aest])
    rows = archive.records("999", "import")
    assert len(rows) == 1
    assert rows[0].start == utc.start
    assert rows[0].start.tzinfo == UTC
    assert rows[0].kwh == 0.4


def test_query_uses_half_open_utc_bounds() -> None:
    archive = IntervalArchive()
    archive.upsert("999", [
        _record("2026-11-01T00:00:00", 0.4),
        _record("2026-11-01T00:30:00", 0.7),
        _record("2026-11-01T01:00:00", 0.5),
    ])
    result = archive.query(
        "999", "import",
        datetime(2026, 11, 1, 0, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
    )
    assert [r.kwh for r in result] == [0.7]


def test_invalid_intervals_rejected() -> None:
    import pytest
    archive = IntervalArchive()
    with pytest.raises(ValueError):
        archive.upsert("999", [
            IntervalRecord(datetime(2026, 11, 1, 0, 15, tzinfo=UTC), "import", 0.1)
        ])
    with pytest.raises(ValueError):
        archive.upsert("999", [
            IntervalRecord(datetime(2026, 11, 1, 0, 30, tzinfo=UTC), "import", float("nan"))
        ])


def test_corrected_slots_survive_roundtrip() -> None:
    archive = IntervalArchive()
    archive.upsert("999", [_record("2026-11-01T00:30:00", 0.4, "export")])
    archive.upsert("999", [_record("2026-11-01T00:30:00", 0.25, "export")])
    restored = IntervalArchive.from_json(archive.to_json())
    assert [r.kwh for r in restored.records("999", "export")] == [0.25]
