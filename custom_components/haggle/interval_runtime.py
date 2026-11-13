"""Optional half-hour AGL archive using Home Assistant storage and HTTP auth.

No new AGL requests, sensors, containers, or Prometheus metrics are created.
The hourly Energy statistics remain the canonical Home Assistant interface.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.storage import Store

from .interval_archive import IntervalArchive, IntervalRecord

_LOGGER = logging.getLogger(__name__)
_ARCHIVES_KEY = "haggle_half_hour_archives"
_VIEW_KEY = "haggle_half_hour_view_registered"


class HalfHourArchive:
    """Per-config-entry persistent index with serialized writes."""

    def __init__(self, hass, entry_id: str, contract: str) -> None:
        self.contract = contract
        self.archive = IntervalArchive()
        self._store = Store(hass, 1, f"haggle_half_hour_{entry_id}")
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        if payload is not None:
            self.archive = IntervalArchive.from_json(payload["archive"])

    async def async_upsert(self, imports, exports) -> None:
        """Persist original AGL slots, replacing corrected values by UTC time."""
        records = [
            IntervalRecord(r.dt, kind, r.kwh, r.cost_aud)
            for kind, readings in (("import", imports), ("export", exports))
            for r in readings
        ]
        if not records:
            return
        async with self._lock:
            self.archive.upsert(self.contract, records)
            await self._store.async_save({"archive": self.archive.to_json()})

    async def async_query(self, kind: str, start: datetime, end: datetime):
        async with self._lock:
            return self.archive.query(self.contract, kind, start, end)


class HaggleIntervalView(HomeAssistantView):
    """Authenticated, bounded, read-only history with original timestamps."""

    url = "/api/haggle/intervals"
    name = "api:haggle:intervals"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        params = request.query
        entry_id = params.get("entry_id")
        kind = params.get("kind", "import")
        if kind not in ("import", "export"):
            raise web.HTTPBadRequest(text="kind must be import or export")
        try:
            start = datetime.fromisoformat(params["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(params["end"].replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("timezone required")
            start, end = start.astimezone(UTC), end.astimezone(UTC)
            if not start < end <= start + timedelta(days=366):
                raise ValueError("invalid interval or range exceeds 366 days")
        except (KeyError, ValueError, OverflowError) as err:
            raise web.HTTPBadRequest(text="valid timezone-aware start/end required") from err

        archives = request.app["hass"].data.get(_ARCHIVES_KEY, {})
        if entry_id is None:
            if len(archives) != 1:
                raise web.HTTPBadRequest(text="entry_id required for multiple Haggle entries")
            archive = next(iter(archives.values()))
        else:
            archive = archives.get(entry_id)
        if archive is None:
            raise web.HTTPNotFound()

        records = await archive.async_query(kind, start, end)
        return self.json({
            "resolution_seconds": 1800,
            "kind": kind,
            "intervals": [
                {
                    "start": record.start.isoformat(),
                    "end": (record.start + timedelta(minutes=30)).isoformat(),
                    "kwh": record.kwh,
                    "average_w": record.kwh * 2000,
                    "cost_aud": record.cost_aud,
                }
                for record in records
            ],
        })


async def async_setup_archive(hass, entry_id: str, contract: str) -> HalfHourArchive:
    archive = HalfHourArchive(hass, entry_id, contract)
    await archive.async_load()
    if not hass.data.get(_VIEW_KEY):
        hass.http.register_view(HaggleIntervalView())
        hass.data[_VIEW_KEY] = True
    hass.data.setdefault(_ARCHIVES_KEY, {})[entry_id] = archive
    return archive


def unload_archive(hass, entry_id: str) -> None:
    hass.data.get(_ARCHIVES_KEY, {}).pop(entry_id, None)
