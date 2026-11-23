# 03 – Disk full

## Symptom

- `preflight.sh`: `[FAIL] disk /var: 312 MB free, need 2048 MB`.
- PostgreSQL: `could not extend file ... No space left on device`, `PANIC: could not write to file "pg_wal/..."`, server stops.
- RabbitMQ: `disk resource limit alarm set`, publishers are blocked (connections in state `blocked`), management UI shows a red disk alarm.
- dnf: `At least ...MB more space needed on the / filesystem`.

## Checks

```bash
df -hT /var /var/lib/pgsql /var/lib/rabbitmq /var/backups /var/log
df -i /var                                           # inodes can run out too
du -xh --max-depth=2 /var | sort -h | tail -n 15
du -sh /var/lib/pgsql/data/pg_wal /var/lib/pgsql/data/log /var/backups/lab/* /var/log/rabbitmq /var/cache/dnf
rabbitmq-diagnostics -q alarms
```

Typical causes: old backups (retention not running), log files without rotation, WAL piling up because of an
inactive replication slot (`select slot_name, active from pg_replication_slots`), dnf cache, a queue with millions
of messages (→ [05](05-queue-backlog.md)).

## Fix

1. Free space without data loss first:
   ```bash
   dnf clean all
   scripts/backup_restore.sh prune --keep 3       # after copying older dumps off the host
   logrotate -f /etc/logrotate.d/lab
   journalctl --vacuum-size=200M                  # systemd hosts
   ```
2. **Never delete files in `pg_wal` by hand.** Remove an unused replication slot instead
   (`select pg_drop_replication_slot('name')`), then `CHECKPOINT`.
3. If that is not enough: extend the volume (LVM: `lvextend -r -L +10G /dev/<vg>/<lv>`).
4. Start the stopped service (→ [01](01-service-does-not-start.md)); RabbitMQ clears the alarm by itself once free space
   is above `disk_free_limit` (1 GB in this lab).

## Verify

```bash
scripts/preflight.sh --ports-mode skip
scripts/healthcheck.sh
rabbitmq-diagnostics -q alarms     # empty
```

## Escalate

Growth is caused by the application (data volume, queue never consumed) → product owner from `inventory.yml`;
capacity planning ticket for a larger volume.
