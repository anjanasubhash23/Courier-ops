# Query timings (2026-11-08)

43,077 shipments and 409,617 tracking events (53 MB including indexes), 30 simulated days at
1,500 bookings a day. Median of 5 runs of `EXPLAIN (ANALYZE, BUFFERS)`, in milliseconds. "Before" is
without the indexes from migration 0006; both detector rows are measured in both states.

| query | used by | before (ms) | after (ms) | speedup |
|---|---|---|---|---|
| detector (before: all events) | every 2 h of simulated time, from cron | 111.64 | 111.88 | - |
| detector (after: live shipments) | same rows as the old query | 22.73 | 19.63 | 1.2x |
| simulated now | most API requests | 8.74 | 0.00 | >100x |
| hubs page | GET /hubs | 24.55 | 0.17 | 141.9x |
| open exceptions, first page | GET /exceptions | 2.19 | 2.21 | - |
| one shipment's timeline | exception detail | 0.01 | 0.01 | - |
| departures in the last 24 h by route | a typical ask question | 8.14 | 4.87 | 1.7x |

**Detector:** 112 ms with the original query, 20 ms now (6x). The gain is the
rewrite, which looks only at live shipments and gets each one's latest event from the existing
`(shipment_id, occurred_at)` index. The new indexes change it by only 3.1 ms.

**Indexes kept:** `ix_tracking_events_occurred_at` (the simulated clock, read on most requests, and time-window
questions) and `ix_shipments_live_last_scan_hub_id` (the hubs page). Both are cheap to maintain: one is on an
append-only column, the other covers only live shipments.

**Partitioning `tracking_events`:** not done. At this size every hot query is an index lookup or touches
only live shipments, so partition pruning has nothing to save. Revisit when the table is in the tens of
millions of rows or old events need to be archived in bulk (detach a month instead of deleting).

Reproduce with `uv run python scripts/explain_report.py`.
