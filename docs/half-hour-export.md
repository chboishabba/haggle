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

## Prometheus / Mimir remote-write (experimental, opt-in)

This fork now includes a remote-write v1 **push** publisher. It does not
install Infinity, modify the normal Home Assistant Prometheus exporter, or
start another service. It sends original half-hour samples directly into an
**existing** compatible Prometheus remote-write receiver / Mimir distributor.

Haggle integration Options now include:

- `prometheus_remote_write_url` — leave blank (default) to disable.
  For Mimir this is normally `http(s)://<mimir>/api/v1/push`.
  A Prometheus server only accepts the push if its remote-write receiver has
  explicitly been enabled; do **not** assume it is already enabled.
- `prometheus_remote_write_bearer` — optional authorization token.

Never configure a URL until the actual receiver endpoint, authentication,
network reachability and allowable **out-of-order window** are verified.
The URL is only used for HTTP POST by HAOS's normal client session, not by
an additional collector or container.

Published metric names, using the original half-hour slot start time:

```text
haggle_grid_import_interval_kwh{contract="...",source="haggle"}
haggle_grid_export_interval_kwh{contract="...",source="haggle"}
haggle_grid_import_average_w{contract="...",source="haggle"}
haggle_grid_export_average_w{contract="...",source="haggle"}
haggle_grid_net_average_w{contract="...",source="haggle"}
```

Import and export metrics are emitted independently. Net power is emitted
only for slots where both directions exist. The signed average watts
calculation is `(import_kwh - export_kwh) * 2000`, positive for import.

A separate HA `Store` delivery ledger, `haggle_prom_write_<entry_id>`,
records samples **after** a 2xx receiver acknowledgment. Unacknowledged
batches retry on the next normal Haggle coordinator refresh; unchanged
samples are not resent. The code never alters existing `haggle:*` Recorder
statistics. HTTP failures do not fail the ordinary Energy import.

**Important unresolved receiver constraints:**

1. AGL data arrives 24–48 hours late and this archive may hold older
   backfill. Default remote-write receivers may reject it as out-of-order
   or too old. Explicitly configure and test the receiver before enabling.
2. AGL's trailing re-fetch may **correct** an already-published half-hour
   value. Prometheus/Mimir rejects a duplicate timestamp with a different
   value. This implementation detects such conflicts and logs them; it does
   **not** silently claim that the historical Prometheus series was corrected.
   The authoritative corrected value remains in the HA archive/API.
3. A recipient may partially ingest an unsuccessful batch before returning
   an error. The delivery ledger advances only after a successful response.
   Replay of identical samples is normally idempotent; test this on the
   receiver before relying on it.
4. This code has not yet been deployed or integration-tested against the
   user's actual Prometheus/Mimir receiver. Do not replace dashboard panels
   until query results, slot timestamps, and correction handling are verified.
5. The stock Prometheus scraper continues collecting live Solarman data
   separately. Do not add a new scrape target for this remote-write feature.

For a safe first receiver test use a **small isolated test series**, verify
historical timestamps by querying the datasource, and check the receiver's
rejection counters and logs before enabling Haggle publishing.
