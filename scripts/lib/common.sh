# shellcheck shell=bash
# Shared helpers for the lab scripts. Sourced, never executed.
#
# Every external path or command is overridable through an environment
# variable so the scripts can be tested without a real PostgreSQL/RabbitMQ.

LAB_ENV_DIR="${LAB_ENV_DIR:-/etc/lab}"
LAB_LOG_DIR="${LAB_LOG_DIR:-/var/log/lab}"
LAB_RUNBOOK_BASE="${LAB_RUNBOOK_BASE:-docs/runbooks}"

# Counters used by the check-style scripts (preflight, healthcheck).
LAB_FAILS=0
LAB_WARNS=0
LAB_CHECKS=0

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*" >&2; }
die() { printf 'ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

ok() { LAB_CHECKS=$((LAB_CHECKS + 1)); printf '[OK]   %s\n' "$*"; }
warn() { LAB_CHECKS=$((LAB_CHECKS + 1)); LAB_WARNS=$((LAB_WARNS + 1)); printf '[WARN] %s\n' "$*"; }
# fail MESSAGE [RUNBOOK]  - RUNBOOK is a file name below docs/runbooks
fail() {
    LAB_CHECKS=$((LAB_CHECKS + 1)); LAB_FAILS=$((LAB_FAILS + 1))
    printf '[FAIL] %s\n' "$1"
    if [ -n "${2:-}" ]; then printf '       runbook: %s/%s\n' "$LAB_RUNBOOK_BASE" "$2"; fi
}

summary() {
    printf '%s: %d checks, %d failed, %d warnings\n' "$1" "$LAB_CHECKS" "$LAB_FAILS" "$LAB_WARNS"
    [ "$LAB_FAILS" -eq 0 ]
}

# Load KEY=VALUE files written by the Ansible roles (/etc/lab/*.env).
# Values already set in the environment win, so callers can override.
load_lab_env() {
    local f line key
    for f in "$LAB_ENV_DIR"/*.env; do
        [ -r "$f" ] || continue
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in ''|'#'*) continue ;; esac
            key="${line%%=*}"
            case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
            if [ -z "${!key+x}" ]; then
                export "$key=${line#*=}"
            fi
        done < "$f"
    done
}

# True when systemd is PID 1 (real VM/host); false in a plain container.
# LAB_SERVICE_MANAGER=systemd|direct forces one mode.
use_systemd() {
    case "${LAB_SERVICE_MANAGER:-auto}" in
        systemd) return 0 ;;
        direct) return 1 ;;
    esac
    [ -d /run/systemd/system ]
}

# Print every TCP port in LISTEN state, one per line, sorted and unique.
# Uses `ss` when available, otherwise parses /proc/net/tcp{,6} directly
# (the minimal almalinux:9 image ships without iproute).
listening_ports() {
    local netdir="${LAB_PROC_NET_DIR:-/proc/net}"
    if [ "${LAB_PORT_SOURCE:-auto}" != proc ] && command -v ss >/dev/null 2>&1; then
        ss -Hltn 2>/dev/null | awk '{print $4}' | sed 's/.*://' | sort -un
        return 0
    fi
    local f
    for f in "$netdir/tcp" "$netdir/tcp6"; do
        [ -r "$f" ] || continue
        # column 2 = local_address (HEXIP:HEXPORT), column 4 = state, 0A = LISTEN
        awk 'NR > 1 && $4 == "0A" { split($2, a, ":"); print a[2] }' "$f"
    done | while read -r hex; do printf '%d\n' "0x$hex"; done | sort -un
}

port_listening() {
    listening_ports | grep -qx "$1"
}
