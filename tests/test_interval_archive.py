from __future__ import annotations

from datetime import UTC, datetime

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
