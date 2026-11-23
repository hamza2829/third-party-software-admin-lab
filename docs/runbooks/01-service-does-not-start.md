# 01 – Service does not start

## Symptom

- `labsvc start postgresql` / `systemctl start postgresql` returns an error or the unit is `failed`.
- `healthcheck.sh` shows `[FAIL] ... service not running` and `port ... not listening`.
- Applications report "connection refused".

## Checks

1. State and last messages
   ```bash
   labsvc status postgresql; labsvc status rabbitmq
   systemctl status postgresql rabbitmq-server --no-pager     # systemd hosts
   journalctl -u postgresql -u rabbitmq-server -n 100 --no-pager
   ```
2. Product logs (the reason is almost always written there)
   ```bash
   tail -n 50 /var/lib/pgsql/data/log/postgresql-*.log /var/log/lab/postgresql/pg_ctl.log
   tail -n 50 /var/log/rabbitmq/rabbit@localhost.log /var/log/lab/rabbitmq-startup.log
   ```
3. Configuration syntax
   ```bash
   runuser -u postgres -- postgres -D /var/lib/pgsql/data -C max_connections   # parses all config files
   rabbitmq-diagnostics -q check_running; rabbitmqctl environment | head        # after a start attempt
   ```
4. Typical causes: port in use (→ [02](02-port-in-use.md)), disk full (→ [03](03-disk-full.md)), wrong ownership of
   `/var/lib/pgsql/data` (must be `postgres:postgres`, mode `0700`), stale `postmaster.pid` after a crash,
   missing `/run/postgresql` (created by tmpfiles on systemd hosts, by `labsvc` in the container), Erlang cookie
   mismatch (`/var/lib/rabbitmq/.erlang.cookie` must be `rabbitmq:rabbitmq 0400`), SELinux denial (`ausearch -m avc -ts recent`).

## Fix

| Cause | Fix |
|-------|-----|
| Invalid setting after a change | Revert the change (snapshot in `/var/lib/lab/snapshots`, or re-run Ansible), then start |
| Stale `postmaster.pid` | Only if **no** postgres process runs (`pgrep -u postgres postgres`): remove the file, start |
| Wrong ownership | `chown -R postgres:postgres /var/lib/pgsql/data; chmod 0700 /var/lib/pgsql/data` |
| Erlang cookie | `chown rabbitmq:rabbitmq /var/lib/rabbitmq/.erlang.cookie; chmod 0400 ...` |
| SELinux | `restorecon -Rv /var/lib/pgsql /var/lib/rabbitmq` – do not disable SELinux |

Then `labsvc start <service>`.

## Verify

```bash
scripts/healthcheck.sh --product postgresql   # or rabbitmq
```

## Escalate

Service still does not start after config revert → vendor/support case with the log excerpt
([vendor-questions.md](../vendor-questions.md#2-support-case)).
