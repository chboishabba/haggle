"""Optional historical remote-write publication through the existing Mimir/Prometheus.

Disabled unless explicitly configured in Haggle options. Never modifies the
hourly Recorder data. A durable delivery ledger avoids duplicate submissions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .prom_remote_write import encode_write_request, records_since, records_to_series

_LOGGER = logging.getLogger(__name__)
OPT_REMOTE_WRITE_URL = "prometheus_remote_write_url"
OPT_REMOTE_WRITE_BEARER = "prometheus_remote_write_bearer"
REMOTE_WRITE_MAX_AGE = timedelta(hours=72)


class HalfHourRemotePublisher:
    def __init__(self, hass, entry_id: str) -> None:
        self._hass = hass
        self._store = Store(hass, 1, f"haggle_prom_write_{entry_id}")
        self._sent: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is not None:
            self._sent = {str(k): float(v) for k, v in data.get("sent", {}).items()}

    async def async_publish(self, archive, options: dict) -> None:
        """Publish missing samples; do not lie about rejected/corrected samples.

        Any HTTP failure leaves the ledger unchanged, allowing a future retry.
        Prometheus/Mimir out-of-order ingestion must be enabled at the receiver.
        """
        url = str(options.get(OPT_REMOTE_WRITE_URL, "")).strip()
        if not url:
            return
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            _LOGGER.error("Haggle remote-write URL must be an HTTP(S) URL without credentials")
            return

        async with self._lock:
            now = datetime.now(UTC)
            imports = records_since(
                archive.archive.records(archive.contract, "import"),
                now=now,
                max_age=REMOTE_WRITE_MAX_AGE,
            )
            exports = records_since(
                archive.archive.records(archive.contract, "export"),
                now=now,
                max_age=REMOTE_WRITE_MAX_AGE,
            )
            groups, pending, conflicts = records_to_series(
                archive.contract, imports, exports, sent=self._sent
            )
            if conflicts:
                _LOGGER.warning(
                    "Haggle remote-write: %d corrected historic samples cannot replace "
                    "already accepted Prometheus values; inspect interval archive",
                    len(conflicts),
                )
            if not pending:
                return
            payload = encode_write_request(groups)
            headers = {
                "Content-Type": "application/x-protobuf",
                "Content-Encoding": "snappy",
                "X-Prometheus-Remote-Write-Version": "0.1.0",
            }
            token = str(options.get(OPT_REMOTE_WRITE_BEARER, "")).strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                session = async_get_clientsession(self._hass)
                async with asyncio.timeout(30):
                    async with session.post(url, data=payload, headers=headers) as response:
                        if response.status not in (200, 204):
                            body = (await response.text())[:250]
                            _LOGGER.error(
                                "Haggle remote-write rejected batch (HTTP %s): %s",
                                response.status, body,
                            )
                            return
                # Only advance after receiver accepted the complete request.
                self._sent.update(pending)
                await self._store.async_save({"sent": self._sent})
            except Exception:
                _LOGGER.exception("Haggle remote-write failed; historical batch will retry")
