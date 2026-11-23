# 02 – Port already in use

## Symptom

- `preflight.sh` before an install: `[FAIL] port 5432: already in use`.
- Service start fails with `could not bind IPv4 address "127.0.0.1": Address already in use` (PostgreSQL) or
  `{error,eaddrinuse}` / `failed_to_listen` (RabbitMQ).

## Checks

1. Who holds the port?
   ```bash
   ss -ltnp 'sport = :5432'          # process name + PID
   ss -ltnp | grep -E ':(5432|5672|15672|25672|4369)\b'
   ps -fp <PID>; rpm -qf "$(readlink -f /proc/<PID>/exe)"
   ```
2. Is it an old instance of the same product (second cluster, previous version still running after an upgrade)?
   ```bash
   pgrep -a postgres; pgrep -a beam.smp
   ```
3. Is another product configured on the same port (e.g. a second PostgreSQL from a vendor bundle)?

## Fix

- Old instance of the same product: stop it cleanly (`labsvc stop postgresql` or `pg_ctl -D <old datadir> stop -m fast`),
  disable its unit so it does not come back after a reboot.
- Other product: agree on a port with its owner. Change the port in Ansible
  (`postgresql_port`, `rabbitmq_amqp_port`, `rabbitmq_management_port`), run the playbook, update the firewalld service
  file (`common_firewall_services`) and inform application owners about the new connection string.
- Never kill an unknown process just to free the port.

## Verify

```bash
scripts/preflight.sh --ports "5432 5672 15672" --ports-mode listening
scripts/healthcheck.sh
```

## Escalate

Port belongs to a third-party product nobody owns → IT Operations lead; check `inventory.yml` for the owner.
