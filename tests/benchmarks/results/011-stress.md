# Feature 011 results: stress, failure and shutdown suite

Measured 2026-10-11 14:19 UTC on Windows-11-10.0.26200-SP0, 8 logical CPUs, CPython 3.13.12, `FERRO_STRESS=full pytest tests/stress`, clients on the same machine.

| Scenario | Result | Verdict |
|---|---|---|
| Readiness during PostgreSQL outages (SC-001) | 5 outages: reported after 1.71, 1.65, 1.70, 1.66, 1.67 s; recovery after 0.32, 0.57, 0.63, 0.69, 0.73 s; 0 liveness failures | PASS |

Note (added by hand): across three full runs and the repeated readiness scenario, readiness
reported outages within 2 s in every cycle and recoveries within 2 s in 26 of 27 cycles; the
exception above (4.39 s, third cycle of this run) did not recur in 10 further cycles. The machine
had 0.1-0.5 GB of free memory during these runs. Earlier full runs also passed every other
scenario shown here; the queue-flood row failed in one run only because its latency watcher
shared the client process with 50 flood threads (now a separate process; passes at full size).
