# 06 – Failed upgrade

## Symptom

- `upgrade.sh` ends with `RESULT: ... unhealthy after upgrade -> rolling back configuration`
  and exit code **3** (config rolled back, service healthy) or **4** (rollback failed).
- Exit code **2**: dnf itself failed (dependency problem, repo not reachable) – nothing was changed.

## Checks

1. What changed?
   ```bash
   ls -lt /var/lib/lab/snapshots/ | head        # snapshot + .versions file per upgrade
   cat /var/lib/lab/snapshots/<product>-<ts>-<pid>.versions
   dnf history list rabbitmq-server postgresql-server
   dnf history info <id>
   rpm -qa --last | head -n 20
   ```
2. Why is it unhealthy? Run `scripts/healthcheck.sh --product <product>` and follow the failing line's runbook.
   Service did not start → product log (→ [01](01-service-does-not-start.md)).
3. Did the package ship new default config? `find /etc/rabbitmq /var/lib/pgsql/data -name '*.rpmnew' -o -name '*.rpmsave'`.
4. Vendor release notes for the target version: removed/renamed settings, required Erlang version (RabbitMQ),
   `pg_upgrade` needed (PostgreSQL **major** versions – a minor update never needs it).

## Fix

- **Exit 3**: the service runs again with the old configuration on the new package version. Decide:
  - adapt the new configuration (compare with `.rpmnew`), test, run `upgrade.sh` again, or
  - downgrade the package as well: `dnf history undo <id>` (check the transaction first), then `labsvc restart <product>`.
- **Exit 4**: snapshot restore did not help.
  1. `dnf history undo <id>` to return to the previous package version.
  2. Restore the config snapshot manually: `tar -C / -xzpf /var/lib/lab/snapshots/<file>.tar.gz`.
  3. PostgreSQL data damaged (very rare on a minor update) → restore the backup taken before the window:
     `scripts/backup_restore.sh restore <dump> --force`.
  4. Open a vendor case immediately (template below) and inform the departments that the window is extended.
- **Exit 2**: read the dnf error. Typical: missing dependency (`erlang` version for a new RabbitMQ – check the vendor's
  compatibility matrix), repo/mirror down (retry later), GPG key changed (import the new key from the vendor, never set `gpgcheck=0`).

## Verify

```bash
scripts/healthcheck.sh
rpm -q postgresql-server rabbitmq-server erlang
```

Close the maintenance window only when the healthcheck is green and note the final versions in `inventory.yml`.

## Escalate

Exit code 4, or exit 3 without a known cause → vendor support case
([vendor-questions.md](../vendor-questions.md#2-support-case)) with the `upgrade.sh` output, the `.versions` file and
the product log from the failed start.
