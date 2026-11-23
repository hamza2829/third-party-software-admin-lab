#!/usr/bin/env bash
# healthcheck.sh - is the installed third-party software actually usable?
#
#   healthcheck.sh [--product postgresql|rabbitmq|all] [--no-smoke]
#
# PostgreSQL: service state, port listening, login with the application
#             user over TCP and a test query.
# RabbitMQ:   service state, AMQP + management ports listening, management
#             API reachable, smoke test (declare queue, publish, consume and
#             compare payload), backlog check of the configured queues.
#
# Connection settings come from /etc/lab/*.env (written by the Ansible roles)
# or from the environment. Exit 0 = healthy, 1 = at least one FAIL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
load_lab_env

PRODUCT=all
SMOKE=1
LABSVC="${LABSVC:-$SCRIPT_DIR/svc.sh}"

while [ $# -gt 0 ]; do
    case "$1" in
        --product) PRODUCT="$2"; shift 2 ;;
        --no-smoke) SMOKE=0; shift ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) die "unknown option: $1" 64 ;;
    esac
done
case "$PRODUCT" in postgresql|rabbitmq|all) ;; *) die "--product must be postgresql, rabbitmq or all" 64 ;; esac

: "${LAB_PG_HOST:=127.0.0.1}" "${LAB_PG_PORT:=5432}" "${LAB_PG_DB:=app_db}" "${LAB_PG_USER:=app_user}"
: "${LAB_RMQ_HOST:=127.0.0.1}" "${LAB_RMQ_PORT:=5672}" "${LAB_RMQ_MGMT_PORT:=15672}"
: "${LAB_RMQ_VHOST:=lab}" "${LAB_RMQ_USER:=lab_admin}" "${LAB_RMQ_QUEUES:=}" "${LAB_RMQ_BACKLOG_WARN:=1000}"

json_get() {  # json_get EXPRESSION  (stdin = JSON document, EXPRESSION uses `d`)
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"
}

check_postgresql() {
    echo "-- postgresql"
    if "$LABSVC" status postgresql >/dev/null 2>&1; then ok "postgresql service running"
    else fail "postgresql service not running" 01-service-does-not-start.md; fi

    if port_listening "$LAB_PG_PORT"; then ok "postgresql port $LAB_PG_PORT listening"
    else fail "postgresql port $LAB_PG_PORT not listening" 01-service-does-not-start.md; fi

    if [ -z "${LAB_PG_APP_PASSWORD:-}" ]; then
        fail "postgresql test query skipped: LAB_PG_APP_PASSWORD not set" 04-wrong-credentials.md
        return
    fi
    local out
    if out="$(PGPASSWORD="$LAB_PG_APP_PASSWORD" PGCONNECT_TIMEOUT=5 psql -X -At \
            -h "$LAB_PG_HOST" -p "$LAB_PG_PORT" -U "$LAB_PG_USER" -d "$LAB_PG_DB" \
            -c "select 'result=' || (1 + 1) || ';version=' || current_setting('server_version')" 2>&1)"; then
        case "$out" in
            result=2*) ok "postgresql test query as $LAB_PG_USER@$LAB_PG_DB: ${out#result=2;}" ;;
            *) fail "postgresql test query returned unexpected output: $out" ;;
        esac
    else
        case "$out" in
            *"password authentication failed"*|*"no pg_hba.conf entry"*)
                fail "postgresql login failed: $(printf '%s' "$out" | head -n 1)" 04-wrong-credentials.md ;;
            *) fail "postgresql test query failed: $(printf '%s' "$out" | head -n 1)" 01-service-does-not-start.md ;;
        esac
    fi
}

rmq_api() {  # rmq_api METHOD PATH [JSON_BODY]
    local args=(-sS -m 10 -u "$LAB_RMQ_USER:${LAB_RMQ_PASSWORD:-}" -H 'content-type: application/json'
                -X "$1" -w '\n%{http_code}')
    [ -n "${3:-}" ] && args+=(--data "$3")
    curl "${args[@]}" "http://$LAB_RMQ_HOST:$LAB_RMQ_MGMT_PORT/api$2"
}
# split "body\nstatus" from rmq_api
body_of() { sed '$d'; }
code_of() { tail -n 1; }

check_rabbitmq() {
    echo "-- rabbitmq"
    if "$LABSVC" status rabbitmq >/dev/null 2>&1; then ok "rabbitmq service running"
    else fail "rabbitmq service not running" 01-service-does-not-start.md; fi

    local port
    for port in "$LAB_RMQ_PORT" "$LAB_RMQ_MGMT_PORT"; do
        if port_listening "$port"; then ok "rabbitmq port $port listening"
        else fail "rabbitmq port $port not listening" 01-service-does-not-start.md; fi
    done

    local resp code
    resp="$(rmq_api GET /overview 2>&1 || true)"
    code="$(printf '%s\n' "$resp" | code_of)"
    case "$code" in
        200) ok "rabbitmq management API: version $(printf '%s\n' "$resp" | body_of | json_get 'd.get("rabbitmq_version","?")')" ;;
        401) fail "rabbitmq management API: HTTP 401 for user $LAB_RMQ_USER" 04-wrong-credentials.md; return ;;
        *) fail "rabbitmq management API not reachable (HTTP ${code:-none})" 01-service-does-not-start.md; return ;;
    esac

    if [ "$SMOKE" = 1 ]; then rabbitmq_smoke; fi

    local q msgs
    for q in $LAB_RMQ_QUEUES; do
        resp="$(rmq_api GET "/queues/$LAB_RMQ_VHOST/$q" || true)"
        if [ "$(printf '%s\n' "$resp" | code_of)" != 200 ]; then
            fail "queue $q missing in vhost $LAB_RMQ_VHOST"; continue
        fi
        msgs="$(printf '%s\n' "$resp" | body_of | json_get 'd.get("messages", 0)')"
        if [ "$msgs" -gt "$LAB_RMQ_BACKLOG_WARN" ]; then
            warn "queue $q backlog: $msgs messages (warn above $LAB_RMQ_BACKLOG_WARN), see $LAB_RUNBOOK_BASE/05-queue-backlog.md"
        else
            ok "queue $q backlog: $msgs messages"
        fi
    done
}

rabbitmq_smoke() {
    local q="lab.healthcheck" payload resp code got
    payload="healthcheck-$(date +%s)-$$"
    # durable + x-expires: the queue removes itself 10 minutes after last use
    code="$(rmq_api PUT "/queues/$LAB_RMQ_VHOST/$q" \
        '{"durable":true,"auto_delete":false,"arguments":{"x-expires":600000}}' | code_of || true)"
    case "$code" in
        201|204) ok "smoke: queue $q declared" ;;
        *) fail "smoke: declare queue $q failed (HTTP $code)"; return ;;
    esac
    resp="$(rmq_api POST "/exchanges/$LAB_RMQ_VHOST/amq.default/publish" \
        "{\"properties\":{},\"routing_key\":\"$q\",\"payload\":\"$payload\",\"payload_encoding\":\"string\"}" || true)"
    if [ "$(printf '%s\n' "$resp" | code_of)" = 200 ] && \
       [ "$(printf '%s\n' "$resp" | body_of | json_get 'd.get("routed")')" = True ]; then
        ok "smoke: message published and routed"
    else
        fail "smoke: publish failed: $(printf '%s\n' "$resp" | body_of)"; return
    fi
    resp="$(rmq_api POST "/queues/$LAB_RMQ_VHOST/$q/get" \
        '{"count":1,"ackmode":"ack_requeue_false","encoding":"auto"}' || true)"
    got="$(printf '%s\n' "$resp" | body_of | json_get 'd[0]["payload"] if d else ""' 2>/dev/null || true)"
    if [ "$got" = "$payload" ]; then ok "smoke: message consumed, payload matches"
    else fail "smoke: consumed payload '$got' does not match '$payload'"; fi
}

echo "== healthcheck ($PRODUCT) =="
case "$PRODUCT" in
    postgresql) check_postgresql ;;
    rabbitmq) check_rabbitmq ;;
    all) check_postgresql; check_rabbitmq ;;
esac
summary healthcheck
