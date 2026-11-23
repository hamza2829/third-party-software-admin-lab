# Runbooks

One page per typical error. Each page has the same structure: **symptom**, **checks** (in the order to run them), **fix**, **verify**, **escalate**.
`scripts/healthcheck.sh` and `scripts/preflight.sh` print the matching runbook next to every `[FAIL]` line.

| # | Runbook | Typical trigger |
|---|---------|-----------------|
| 01 | [Service does not start](01-service-does-not-start.md) | `[FAIL] postgresql service not running` |
| 02 | [Port already in use](02-port-in-use.md) | `[FAIL] port 5432: already in use` |
| 03 | [Disk full](03-disk-full.md) | `[FAIL] disk /var: ... need ... MB`, RabbitMQ blocks publishers |
| 04 | [Wrong credentials](04-wrong-credentials.md) | `password authentication failed`, HTTP 401 |
| 05 | [Queue backlog](05-queue-backlog.md) | `[WARN] queue orders.incoming backlog` |
| 06 | [Failed upgrade](06-failed-upgrade.md) | `upgrade.sh` exit code 3 or 4 |

Commands use `labsvc` (= `scripts/svc.sh`), which calls `systemctl` on a normal host and drives the daemons directly in a container without systemd. On a host you can use `systemctl` / `journalctl` directly.
