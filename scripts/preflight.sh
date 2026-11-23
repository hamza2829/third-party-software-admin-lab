#!/usr/bin/env bash
# preflight.sh - checks before installing or upgrading third-party software.
#
#   preflight.sh [--min-disk-mb N] [--min-mem-mb N] [--paths "/var /opt"]
#                [--ports "5432 5672"] [--ports-mode free|listening|skip]
#
# Checks: OS release (RHEL-compatible, major >= 8), package manager (dnf),
# free disk space per path, available memory, TCP ports (free before an
# install, listening after it). Exit 0 = all good, 1 = at least one FAIL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

MIN_DISK_MB="${LAB_MIN_DISK_MB:-2048}"
MIN_MEM_MB="${LAB_MIN_MEM_MB:-1024}"
PATHS="${LAB_PREFLIGHT_PATHS:-/var /opt}"
PORTS="${LAB_PREFLIGHT_PORTS:-5432 5672 15672 25672 4369}"
PORTS_MODE=free
OS_RELEASE_FILE="${LAB_OS_RELEASE_FILE:-/etc/os-release}"
MEMINFO_FILE="${LAB_MEMINFO_FILE:-/proc/meminfo}"

while [ $# -gt 0 ]; do
    case "$1" in
        --min-disk-mb) MIN_DISK_MB="$2"; shift 2 ;;
        --min-mem-mb) MIN_MEM_MB="$2"; shift 2 ;;
        --paths) PATHS="$2"; shift 2 ;;
        --ports) PORTS="$2"; shift 2 ;;
        --ports-mode) PORTS_MODE="$2"; shift 2 ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) die "unknown option: $1" 64 ;;
    esac
done
case "$PORTS_MODE" in free|listening|skip) ;; *) die "--ports-mode must be free, listening or skip" 64 ;; esac

check_os() {
    if [ ! -r "$OS_RELEASE_FILE" ]; then
        fail "OS release: $OS_RELEASE_FILE not readable"; return
    fi
    local ID="" ID_LIKE="" VERSION_ID="" PRETTY_NAME=""
    # shellcheck source=/dev/null
    . "$OS_RELEASE_FILE"
    local major="${VERSION_ID%%.*}"
    case " $ID $ID_LIKE " in
        *" rhel "*|*" almalinux "*|*" rocky "*|*" centos "*|*" fedora "*)
            if [ "${major:-0}" -ge 9 ] 2>/dev/null; then
                ok "OS release: ${PRETTY_NAME:-$ID $VERSION_ID} (RHEL compatible)"
            elif [ "${major:-0}" -ge 8 ] 2>/dev/null; then
                warn "OS release: ${PRETTY_NAME:-$ID $VERSION_ID} is EL8, lab is tested on EL9"
            else
                fail "OS release: ${PRETTY_NAME:-$ID $VERSION_ID} is too old (need EL8+)"
            fi ;;
        *) fail "OS release: ${PRETTY_NAME:-$ID} is not RHEL compatible (ID=$ID ID_LIKE=$ID_LIKE)" ;;
    esac
}

check_pkg_manager() {
    if command -v dnf >/dev/null 2>&1; then ok "package manager: dnf found"
    else fail "package manager: dnf not found"; fi
}

check_disk() {
    local p probe avail
    for p in $PATHS; do
        probe="$p"
        while [ ! -e "$probe" ] && [ "$probe" != / ]; do probe="$(dirname "$probe")"; done
        avail="$(df -Pm "$probe" | awk 'NR == 2 {print $4}')"
        if [ -z "$avail" ]; then
            fail "disk $p: could not read free space"
        elif [ "$avail" -ge "$MIN_DISK_MB" ]; then
            ok "disk $p: ${avail} MB free (min ${MIN_DISK_MB} MB)"
        else
            fail "disk $p: ${avail} MB free, need ${MIN_DISK_MB} MB" 03-disk-full.md
        fi
    done
}

check_memory() {
    local kb mb
    kb="$(awk '/^MemAvailable:/ {print $2}' "$MEMINFO_FILE" 2>/dev/null || true)"
    if [ -z "$kb" ]; then fail "memory: MemAvailable missing in $MEMINFO_FILE"; return; fi
    mb=$((kb / 1024))
    if [ "$mb" -ge "$MIN_MEM_MB" ]; then ok "memory: ${mb} MB available (min ${MIN_MEM_MB} MB)"
    else fail "memory: ${mb} MB available, need ${MIN_MEM_MB} MB"; fi
}

check_ports() {
    [ "$PORTS_MODE" = skip ] && return 0
    local open port
    open="$(listening_ports)"
    for port in $PORTS; do
        if printf '%s\n' "$open" | grep -qx "$port"; then
            if [ "$PORTS_MODE" = free ]; then fail "port $port: already in use" 02-port-in-use.md
            else ok "port $port: listening"; fi
        else
            if [ "$PORTS_MODE" = free ]; then ok "port $port: free"
            else fail "port $port: nothing listening" 01-service-does-not-start.md; fi
        fi
    done
}

echo "== preflight on $(hostname 2>/dev/null || cat /proc/sys/kernel/hostname) =="
check_os
check_pkg_manager
check_disk
check_memory
check_ports
summary preflight
