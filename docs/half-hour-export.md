# Optional half-hour AGL history archive (fork feature)

This fork preserves the original AGL half-hour import/export slots alongside
Haggle's existing **hourly Home Assistant Recorder statistics**. No changes
to the Energy dashboard or to the integration's AGL authentication are needed.

## Design

- Original `IntervalReading` batches enter `_import_sweep_batches` and continue
  through the unchanged hourly Recorder import.
- After Recorder succeeds, the batches are upserted by
  `(contract, kind, UTC start)` into HA's `Store` file
  `/config/.storage/haggle_half_hour_<entry_id>`.
- Re-fetched intervals replace the same timestamp. The archive is restored
  before the first AGL refresh, and no new AGL requests are made.
- This archives **newly fetched or re-fetched** intervals; it cannot recover
  older 30-minute slots from already-aggregated hourly Recorder statistics.
  Haggle's normal backfill and trailing rewindow will gradually populate it.
- Existing `haggle:*` Recorder statistics remain the authority for HA Energy.

## Read-only HTTP API

HA's built-in HTTP server registers:

`GET /api/haggle/intervals?kind=import&start=2026-11-01T00:00:00Z&end=2026-11-02T00:00:00Z`

Authentication: a valid Home Assistant bearer token (handled by HA's
`HomeAssistantView.requires_auth`). The endpoint does **not** accept
AGL credentials or make AGL requests.

Parameters:
- `kind`: `import`, `export`, or `net`.
- `start`, `end`: timezone-aware ISO 8601 UTC or offset bounds;
  half-open range, maximum 366 days.
- `entry_id`: needed only when several Haggle config entries exist.

Import/export rows include `start`, `end`, `kwh`, `average_w`,
and `cost_aud`. For 30-minute slots, `average_w = kwh * 2000`.

`net` rows include `import_kwh`, `export_kwh`, `net_kwh`, and
`net_average_w = (import_kwh - export_kwh) * 2000`.
A net row is returned **only when both** directional readings exist for
that half-hour. Absent readings are not treated as zero.

## Limitations / deployment gating

- This endpoint is historical; AGL readings remain delayed by 24–48 hours.
- A new Grafana datasource or historical ingestion strategy is still required.
  The stock HA Prometheus entity exporter **does not** automatically export
  this API's history. Do not claim the Grafana bridge is complete.
- Before any HAOS deployment, back up HA and check the fork's version and HA
  compatibility. The existing Haggle config entry and token must be left alone.
- `Store` serializes the retained archive as a whole. For very long retention,
  measure archive size and consider a paged persistence backend.
- A failed archive write is logged and does not stop the existing HA Energy
  import. Check HA logs after deployment before relying on the archive.
- The endpoint is protected by normal HA authentication, not restricted to
  HA administrators; tighten this if the archive must be administrator-only.
