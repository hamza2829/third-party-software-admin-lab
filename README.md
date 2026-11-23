# third-party-software-admin-lab

How third-party products are **installed, administered and updated** on a RedHat-compatible Linux
(AlmaLinux / Rocky Linux 9), shown with PostgreSQL and RabbitMQ:

- **Ansible roles** (idempotent): `common`, `postgresql`, `rabbitmq`
- **Bash admin tools**: preflight, healthcheck with smoke test, backup/restore, upgrade with config rollback
- **Python 3.11 maintenance planner**: inventory → maintenance windows + communication per department
- **Runbooks** for six typical errors and **templates for vendor questions**
- **GitHub Actions**: everything runs in an `almalinux:9` and a `rockylinux:9` container, Ansible twice, all checks

> **Honest note.** This is a lab that runs in a CI container, not a production server.
> The container has no systemd, no firewalld, no SELinux enforcement, a single node, and it is thrown away after
> every run. The scripts detect the missing systemd and start the daemons directly (`scripts/svc.sh`); on a real
> AlmaLinux/Rocky host the same scripts use `systemctl`. Firewall rules are only written as firewalld config files.
> Passwords in `ansible/group_vars/all.yml` are lab defaults, overridable via environment variables – there are no
> real secrets, keys or accounts in this repository. Owners, vendors and departments in `inventory.yml` are fictional.

## Results of the CI run

<!-- RESULTS:START -->
From the green CI run [45177855841](https://github.com/hamza2829/third-party-software-admin-lab/actions/runs/45177855841)
(23 Nov 2026), taken from the job logs and the uploaded `lab-results` artifacts.

Static job: shellcheck, yamllint, ansible-lint (production profile), syntax check all passed; pytest `91 passed`;
maintenance plan unchanged.

| | `almalinux:9` | `rockylinux/rockylinux:9` |
|---|---|---|
| OS | AlmaLinux 9.9 (Chartreuse Bobcat) | Rocky Linux 9.9 (Blue Onyx) |
| PostgreSQL | 16.15 (`postgresql-server-16.15-1.module_el9.8.0+311+c39b793b`) | 16.15 (`postgresql-server-16.15-1.module+el9.8.0+40320+f6f9ed3c`) |
| RabbitMQ installed first | 4.4.0 (`rabbitmq-server-4.4.0-1.el8`) | 4.4.0 (`rabbitmq-server-4.4.0-1.el8`) |
| Erlang/OTP (from the RabbitMQ startup log) | 29.1.2 | 29.1.2 |
| Ansible run 1 | `ok=42 changed=36 unreachable=0 failed=0 skipped=2` | `ok=42 changed=36 unreachable=0 failed=0 skipped=2` |
| Ansible run 2 | `ok=38 changed=0 unreachable=0 failed=0 skipped=3` | `ok=38 changed=0 unreachable=0 failed=0 skipped=3` |
| Healthcheck after install | 13 checks, 0 failed, 0 warnings | 13 checks, 0 failed, 0 warnings |
| Preflight, ports busy (expected to fail) | 7 checks, 2 failed | 7 checks, 2 failed |
| Wrong password | detected (`password authentication failed`) | detected (`password authentication failed`) |
| Backup verify | `verify ok`: restores and matches row counts (orders=2500, customers=2500) | `verify ok`: restores and matches row counts (orders=2500, customers=2500) |
| RabbitMQ upgrade | 4.4.0 → 4.4.1, healthcheck 10 checks, 0 failed | 4.4.0 → 4.4.1, healthcheck 10 checks, 0 failed |
| PostgreSQL upgrade | already latest (16.15), healthy | already latest (16.15), healthy |
| Broken config → rollback | config restored, healthy again, `upgrade.sh exit code: 3` | config restored, healthy again, `upgrade.sh exit code: 3` |
| Final healthcheck | 13 checks, 0 failed, 0 warnings | 13 checks, 0 failed, 0 warnings |

Fixes needed for the first real AlmaLinux/Rocky run: the `labsvc` symlink did not find `lib/common.sh`
(scripts now resolve symlinks), the PostgreSQL log directory mode differed between the role and `svc.sh`
(second run reported `changed=1`), and logrotate refused the group-writable `/var/log/lab` without a `su` line.
<!-- RESULTS:END -->

## Layout

```
ansible/                    site.yml, inventory, roles/{common,postgresql,rabbitmq}
scripts/
  preflight.sh              OS release, dnf, disk, memory, ports (free before install / listening after)
  healthcheck.sh            service state, ports, SQL test query, RabbitMQ API + publish/consume smoke test, backlog
  backup_restore.sh         pg_dump -Fc + sha256, list, prune, verify (restore into temp DB, compare row counts), restore
  upgrade.sh                snapshot configs -> dnf upgrade -> restart -> healthcheck -> rollback config on failure
  svc.sh                    start/stop/status with systemctl, or directly when systemd is not PID 1 (container)
  bootstrap.sh              python3.11 venv with Ansible on a fresh EL9 host
  run_playbook.sh           runs the playbook, evaluates PLAY RECAP, --assert-idempotent
tools/maintenance_planner.py   inventory.yml -> docs/maintenance-plan.md
docs/runbooks/              01..06 typical errors: symptom, checks, fix, verify, escalate
docs/vendor-questions.md    templates: version question, support case, log excerpt, steps to reproduce
tests/                      pytest: scripts against stub commands, planner, templates, docs
```

## What the roles do

| Role | Tasks |
|------|-------|
| `common` | base packages, group `labadmin`, user `labops`, directories (`/opt/lab`, `/etc/lab`, `/var/log/lab`, `/var/backups/lab`, snapshots), installs the admin scripts to `/opt/lab/bin` + `labsvc` link, logrotate config, firewalld service + zone XML (config only) |
| `postgresql` | enables the AppStream module stream `postgresql:16`, installs via dnf, `initdb` (no `postgresql-setup`, it needs systemd), `conf.d/10-lab.conf`, `pg_hba.conf` (SCRAM for the app, peer + ident map for admins, explicit reject at the end), `pg_ident.conf`, starts the service, creates role `app_user` and database `app_db` |
| `rabbitmq` | imports the vendor signing keys, configures Team RabbitMQ's Erlang + RabbitMQ repositories, installs (optionally a pinned version), `rabbitmq.conf`, `rabbitmq-env.conf`, enables the management plugin, starts, creates vhost `lab`, admin + application user with least-privilege permissions, deletes `guest`, declares queues incl. dead-lettering |

Idempotency: every `command` task has `creates:` or a `changed_when:`; service start goes through
`labsvc ensure-started`, which reports `STATE=running` when nothing had to be done. CI fails if the second run
reports anything other than `changed=0`.

## Run it on an AlmaLinux / Rocky 9 host

```bash
sudo scripts/preflight.sh
sudo scripts/bootstrap.sh
sudo PATH=/opt/lab/venv/bin:$PATH scripts/run_playbook.sh
sudo PATH=/opt/lab/venv/bin:$PATH scripts/run_playbook.sh --assert-idempotent
sudo scripts/healthcheck.sh
sudo scripts/backup_restore.sh backup && sudo scripts/backup_restore.sh verify
sudo scripts/upgrade.sh --dry-run rabbitmq
sudo scripts/upgrade.sh rabbitmq
```

Local checks without a RedHat system (any Linux/macOS with Python 3.11+):

```bash
pip install -r requirements-dev.txt
make check          # shellcheck, yamllint, ansible-lint (production profile), pytest
make plan           # regenerate docs/maintenance-plan.md
```

## Upgrade and rollback

`scripts/upgrade.sh <product>`:

1. records package versions (`rpm -q`)
2. snapshots the product's config (`/etc/rabbitmq`, or `postgresql.conf`, `pg_hba.conf`, `pg_ident.conf`, `conf.d`) as tar.gz + sha256 + `.versions`
3. `dnf -y upgrade` (or `--to VERSION`)
4. restarts the service and runs `healthcheck.sh --product <product>`
5. on failure: stops the service, restores the snapshot (files added by the upgrade are removed), restarts, checks again

Exit codes: `0` ok, `2` dnf failed (nothing changed), `3` config rolled back and healthy, `4` rollback failed – escalate.
The rollback covers configuration only. The package stays on the new version; `dnf history undo` is the documented
manual next step ([runbook 06](docs/runbooks/06-failed-upgrade.md)). CI exercises the rollback with
`--simulate-broken-config`, which appends an invalid setting after the package step.

## Maintenance planner

```bash
python3 tools/maintenance_planner.py --start 2026-12-01 --output docs/maintenance-plan.md
python3 tools/maintenance_planner.py --start 2026-12-01 --format json
```

Rules: dependencies first (Erlang before RabbitMQ, database and broker before the ERP connector), low criticality
first among independent products, at least `lead_time_days` after the start, the product's preferred weekday,
no global blackouts (year-end freeze), no department freezes (Finance month-end closing), at most one change per day.
Output: schedule table with change type (major/minor/patch) and risk, constraints, and one ready-to-send message per
department. See [docs/maintenance-plan.md](docs/maintenance-plan.md).

## Limits of the lab

- One node; no replication, no RabbitMQ cluster, no HA.
- No systemd/journald, SELinux and firewalld in the container: those code paths (`systemctl` in `svc.sh`, the
  `systemd_service` tasks) are written for real hosts but only the non-systemd path runs in CI.
- The PostgreSQL upgrade in CI is usually a no-op (the container installs the newest AppStream build). The RabbitMQ
  upgrade is real: CI installs the previous release from the vendor repository first and upgrades to the latest.
- Package downgrade on rollback is not automated on purpose.

## License

MIT
