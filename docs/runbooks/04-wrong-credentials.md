# 04 – Wrong credentials

## Symptom

- `healthcheck.sh`: `[FAIL] postgresql login failed: ... password authentication failed for user "app_user"`.
- `no pg_hba.conf entry for host "10.1.2.3", user "app_user", database "app_db"` – connection from a network that is not allowed.
- RabbitMQ: `HTTP 401` from the management API, `ACCESS_REFUSED - Login was refused` in the client, or
  `access to vhost 'lab' refused for user`.

## Checks

1. Which credentials does the client use? (application config, `/etc/lab/*.env` for the admin scripts – mode `0600`)
2. PostgreSQL server side:
   ```bash
   grep -E 'authentication|pg_hba' /var/lib/pgsql/data/log/postgresql-*.log | tail
   psql -U postgres -c "select rolname, rolcanlogin, rolvaliduntil from pg_roles where rolname = 'app_user'"
   psql -U postgres -c "select * from pg_hba_file_rules where error is not null or database @> '{app_db}'"
   ```
   `password authentication failed` = user exists, password wrong or expired.
   `no pg_hba.conf entry` = address/database/user combination is not allowed (see `ansible/roles/postgresql/templates/pg_hba.conf.j2`).
3. RabbitMQ:
   ```bash
   rabbitmqctl list_users
   rabbitmqctl list_permissions -p lab
   rabbitmqctl authenticate_user lab_admin '<password>'
   ```

## Fix

- Password changed on purpose: update the client, or set the new value through Ansible
  (`LAB_PG_APP_PASSWORD` / `LAB_RMQ_*_PASSWORD` environment variables or vault) and run the playbook.
- Reset (PostgreSQL): `psql -U postgres -c "\password app_user"`; (RabbitMQ): `rabbitmqctl change_password lab_admin '<new>'`.
  Note: the RabbitMQ role uses `update_password: on_create`, so Ansible does not overwrite a password changed by hand.
- Missing network: add it to `postgresql_hba_networks`, run the playbook (reload only, no restart).
- Missing vhost permission: `rabbitmqctl set_permissions -p lab <user> '<conf>' '<write>' '<read>'` or fix the role variables.
- Never switch `pg_hba.conf` to `trust` to "fix" a login.

## Verify

```bash
scripts/healthcheck.sh
```

## Escalate

Credentials are owned by the vendor's application installer (embedded user) → support case, ask for the documented
procedure to rotate them.
