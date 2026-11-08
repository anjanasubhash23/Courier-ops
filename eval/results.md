# Eval results

<!-- detector:start -->
## detector (2026-11-08)

8614 shipments, 1280 with an injected fault. Detector replayed every 2 h of simulated time.

**Shipment level:** 1239 flagged, precision 100.0%, recall 96.8%.

Recall by fault type (time to detect is from the fault starting to the shipment's first exception):

| fault_type | faults | detected | recall_pct | median_hours_to_detect |
|---|---|---|---|---|
| failed_delivery | 470 | 470 | 100.0 | 1.0 |
| hub_stuck | 254 | 245 | 96.5 | 11.8 |
| lost | 31 | 30 | 96.8 | 4.4 |
| misroute | 67 | 48 | 71.6 | 31.6 |
| missed_connection | 285 | 278 | 97.5 | 11.0 |
| pickup_delay | 173 | 168 | 97.1 | 16.0 |

Precision by rule:

| rule | exceptions | on_faulty_shipments | precision_pct | fault_types |
|---|---|---|---|---|
| delivery_failed | 788 | 788 | 100.0 | failed_delivery |
| in_transit_overdue | 30 | 30 | 100.0 | lost |
| misrouted | 46 | 46 | 100.0 | misroute |
| pickup_overdue | 168 | 168 | 100.0 | pickup_delay |
| sla_breach | 438 | 438 | 100.0 | failed_delivery, hub_stuck, lost, misroute, missed_connection, pickup_delay |
| stuck_at_delivery_center | 88 | 88 | 100.0 | hub_stuck |
| stuck_at_hub | 435 | 435 | 100.0 | hub_stuck, missed_connection |

Of the 41 missed faults, 25 began in the last 24 h of the run, before any rule could fire, and
14 were delivered without tripping one (misroutes whose detour neither reached a wrong delivery center
nor passed a hub twice, and still arrived on time).

Precision is 100.0% largely because the simulator never delays a fault-free shipment, and the thresholds
sit just above its normal timings. Real networks are noisier, so expect lower precision on real data.
<!-- detector:end -->

<!-- text-to-sql:start -->
## text-to-sql (2026-11-08)

**Guard:** 27/27 adversarial queries rejected before execution.

**Gold SQL:** all 20 gold queries pass the guard and run as `ask_reader`.


**Model accuracy:** skipped, no LLM configured (`LLM_PROVIDER=none`).
<!-- text-to-sql:end -->

<!-- triage:start -->
## triage (2026-11-08)

Each faulty shipment's first exception, triaged as of its last sighting. The rules' thresholds come from the simulator's timings, so they set a high bar on simulated data; the useful comparison is how close a model gets without being told them.

**Rules (baseline, no model):** Cause accuracy 1235/1239 (99.7%). 2194 actions proposed, all within policy; 0 dropped by validation.

| fault | cases | correct | accuracy | diagnosed instead |
|---|---|---|---|---|
| failed_delivery | 470 | 470 | 100% | - |
| hub_stuck | 245 | 242 | 99% | missed_connection 3 |
| lost | 30 | 30 | 100% | - |
| misroute | 48 | 48 | 100% | - |
| missed_connection | 278 | 277 | 100% | pickup_delay 1 |
| pickup_delay | 168 | 168 | 100% | - |

**LLM:** skipped, no LLM configured (`LLM_PROVIDER=none`).
<!-- triage:end -->
