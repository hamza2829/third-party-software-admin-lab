#!/usr/bin/env bash
# svc.sh - start/stop/status for the lab services, with or without systemd.
#
#   svc.sh {start|stop|restart|status|ensure-started} {postgresql|rabbitmq}
#
# On a normal AlmaLinux/Rocky host (systemd is PID 1) this is a thin wrapper
# around systemctl. Inside a container without systemd (GitHub Actions job
# container) it drives the daemons directly: pg_ctl for PostgreSQL and a
# detached rabbitmq-server process for RabbitMQ.
#
# ensure-started prints STATE=running (nothing done) or STATE=started, which
# the Ansible roles use for changed_when, so a second run reports no change.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

PGDATA="${LAB_PGDATA:-/var/lib/pgsql/data}"
PG_LOG="${LAB_PG_CTL_LOG:-$LAB_LOG_DIR/postgresql/pg_ctl.log}"
PG_RUNDIR="${LAB_PG_RUNDIR:-/run/postgresql}"
RMQ_LOG="${LAB_RMQ_STARTUP_LOG:-$LAB_LOG_DIR/rabbitmq-startup.log}"
RMQ_TIMEOUT="${LAB_RMQ_START_TIMEOUT:-120}"

usage() { echo "usage: $0 {start|stop|restart|status|ensure-started} {postgresql|rabbitmq}" >&2; exit 64; }

unit_name() {
    case "$1" in
        postgresql) echo postgresql ;;
        rabbitmq) echo rabbitmq-server ;;
    esac
}

as_postgres() {
    if [ "$(id -un)" = postgres ] || [ "${LAB_PG_RUN_AS-postgres}" = "" ]; then
        "$@"
    else
        runuser -u postgres -- "$@"
    fi
}

# ---- PostgreSQL without systemd ------------------------------------------
pg_status() { as_postgres pg_ctl -D "$PGDATA" status >/dev/null 2>&1; }
pg_start() {
    if [ "$(id -u)" = 0 ] && id postgres >/dev/null 2>&1; then
        install -d -o postgres -g postgres -m 0755 "$PG_RUNDIR"
        install -d -o postgres -g postgres -m 0750 "$(dirname "$PG_LOG")"
    fi
    as_postgres pg_ctl -D "$PGDATA" -l "$PG_LOG" -w -t 60 start
}
pg_stop() { as_postgres pg_ctl -D "$PGDATA" -m fast -w -t 60 stop; }

# ---- RabbitMQ without systemd --------------------------------------------
rmq_status() { rabbitmq-diagnostics -q check_running >/dev/null 2>&1; }
rmq_start() {
    mkdir -p "$(dirname "$RMQ_LOG")"
    # setsid detaches the broker from this shell (and from Ansible's pipes).
    setsid -f sh -c "exec rabbitmq-server >>'$RMQ_LOG' 2>&1" </dev/null
    local waited=0
    until rabbitmq-diagnostics -q ping >/dev/null 2>&1; do
        sleep 2; waited=$((waited + 2))
        if [ "$waited" -ge "$RMQ_TIMEOUT" ]; then
            echo "rabbitmq did not answer ping within ${RMQ_TIMEOUT}s, last log lines:" >&2
            tail -n 30 "$RMQ_LOG" >&2 || true
            return 1
        fi
    done
    rabbitmqctl -q await_startup --timeout "$RMQ_TIMEOUT"
}
rmq_stop() { rabbitmqctl -q shutdown; }

# ---- dispatch -------------------------------------------------------------
do_action() {
    local action="$1" svc="$2"
    if use_systemd; then
        case "$action" in
            status) systemctl is-active --quiet "$(unit_name "$svc")" ;;
            *) systemctl "$action" "$(unit_name "$svc")" ;;
        esac
        return
    fi
    local p
    case "$svc" in postgresql) p=pg ;; rabbitmq) p=rmq ;; esac
    case "$action" in
        status) "${p}_status" ;;
        start) "${p}_status" || "${p}_start" ;;
        stop) if "${p}_status"; then "${p}_stop"; fi ;;
        restart) if "${p}_status"; then "${p}_stop"; fi; "${p}_start" ;;
    esac
}

[ $# -eq 2 ] || usage
ACTION="$1"; SVC="$2"
case "$SVC" in postgresql|rabbitmq) ;; *) usage ;; esac

case "$ACTION" in
    status)
        if do_action status "$SVC"; then echo "$SVC: running"; else echo "$SVC: stopped"; exit 3; fi ;;
    start|stop|restart)
        do_action "$ACTION" "$SVC" ;;
    ensure-started)
        if do_action status "$SVC"; then
            echo "STATE=running"
        else
            do_action start "$SVC"
            echo "STATE=started"
        fi ;;
    *) usage ;;
esac
