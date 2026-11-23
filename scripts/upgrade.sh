#!/usr/bin/env bash
# upgrade.sh - controlled package upgrade with config snapshot and rollback.
#
#   upgrade.sh [--dry-run] [--to VERSION] [--simulate-broken-config] postgresql|rabbitmq
#
#  1. record installed package versions
#  2. snapshot the product's config files (tar.gz + sha256) to the snapshot dir
#  3. dnf upgrade (or install --to VERSION)
#  4. restart the service and run healthcheck.sh for the product
#  5. healthcheck failed -> restore the config snapshot, restart, check again
#
# --simulate-broken-config appends an invalid setting after the package step,
# the way a bad config change shipped with an upgrade would. It exists to
# exercise the rollback path in CI.
#
# Exit codes: 0 upgraded and healthy, 2 dnf failed (nothing changed),
#             3 healthcheck failed and config rollback succeeded,
#             4 rollback failed too (escalate), 64 usage error.
# Rollback covers configuration only; the package stays at the new version
# (`dnf history undo` is the manual next step, see runbook 06).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
load_lab_env

DNF="${LAB_DNF:-dnf}"
RPM="${LAB_RPM:-rpm}"
LABSVC="${LABSVC:-$SCRIPT_DIR/svc.sh}"
HEALTHCHECK="${LAB_HEALTHCHECK:-$SCRIPT_DIR/healthcheck.sh}"
SNAPSHOT_DIR="${LAB_SNAPSHOT_DIR:-/var/lib/lab/snapshots}"
PGDATA="${LAB_PGDATA:-/var/lib/pgsql/data}"

DRY_RUN=0; SIMULATE=0; TO_VERSION=""; PRODUCT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --simulate-broken-config) SIMULATE=1; shift ;;
        --to) TO_VERSION="$2"; shift 2 ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        -*) die "unknown option: $1" 64 ;;
        *) PRODUCT="$1"; shift ;;
    esac
done

case "$PRODUCT" in
    postgresql)
        PACKAGES="${LAB_UPGRADE_PACKAGES:-postgresql-server postgresql}"
        CONFIG_PATHS="${LAB_UPGRADE_CONFIG_PATHS:-$PGDATA/postgresql.conf $PGDATA/pg_hba.conf $PGDATA/pg_ident.conf $PGDATA/conf.d}"
        BROKEN_FILE="${LAB_UPGRADE_BROKEN_FILE:-$PGDATA/postgresql.conf}"
        BROKEN_LINE="lab_simulated_broken_param = on" ;;
    rabbitmq)
        PACKAGES="${LAB_UPGRADE_PACKAGES:-rabbitmq-server}"
        CONFIG_PATHS="${LAB_UPGRADE_CONFIG_PATHS:-/etc/rabbitmq}"
        BROKEN_FILE="${LAB_UPGRADE_BROKEN_FILE:-/etc/rabbitmq/rabbitmq.conf}"
        BROKEN_LINE="lab.simulated.broken.setting = true" ;;
    *) die "usage: upgrade.sh [--dry-run] [--to VERSION] [--simulate-broken-config] postgresql|rabbitmq" 64 ;;
esac
read -r -a PKGS <<< "$PACKAGES"
MAIN_PKG="${PKGS[0]}"

versions() {
    local p
    for p in "${PKGS[@]}"; do
        printf '%s=%s\n' "$p" "$("$RPM" -q --qf '%{VERSION}-%{RELEASE}' "$p" 2>/dev/null || echo not-installed)"
    done
}

health() { "$HEALTHCHECK" --product "$PRODUCT"; }

snapshot() {
    mkdir -p "$SNAPSHOT_DIR"
    SNAP="$SNAPSHOT_DIR/${PRODUCT}-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
    local rel=() p
    for p in $CONFIG_PATHS; do
        [ -e "$p" ] || { echo "snapshot: skipping missing $p"; continue; }
        rel+=("${p#/}")
    done
    [ "${#rel[@]}" -gt 0 ] || die "nothing to snapshot for $PRODUCT" 1
    tar -C / -czpf "$SNAP" "${rel[@]}"
    (cd "$SNAPSHOT_DIR" && sha256sum "$(basename "$SNAP")" > "$(basename "$SNAP").sha256")
    versions > "${SNAP%.tar.gz}.versions"
    echo "snapshot: $SNAP (${#rel[@]} paths)"
}

restore_snapshot() {
    (cd "$SNAPSHOT_DIR" && sha256sum -c --status "$(basename "$SNAP").sha256") \
        || { echo "snapshot checksum mismatch: $SNAP"; return 1; }
    local p listing
    listing="$(tar -tzf "$SNAP")"
    for p in $CONFIG_PATHS; do
        # remove the current version so files added by the upgrade disappear too
        if printf '%s\n' "$listing" | grep -Fxq -e "${p#/}" -e "${p#/}/"; then
            rm -rf "$p"
        fi
    done
    tar -C / -xzpf "$SNAP"
    echo "config restored from $(basename "$SNAP")"
}

echo "== upgrade $PRODUCT ($(ts)) =="
BEFORE="$(versions)"
echo "installed before:"; printf '%s\n' "$BEFORE" | sed 's/^/  /'

if [ "$DRY_RUN" = 1 ]; then
    echo "dry-run: would snapshot: $CONFIG_PATHS"
    rc=0; "$DNF" -q check-update "${PKGS[@]}" || rc=$?
    case "$rc" in
        0) echo "dry-run: no updates available for: $PACKAGES" ;;
        100) echo "dry-run: updates available (see list above)" ;;
        *) echo "dry-run: dnf check-update failed (rc=$rc)"; exit 2 ;;
    esac
    exit 0
fi

snapshot

if [ -n "$TO_VERSION" ]; then
    echo "dnf: installing $MAIN_PKG-$TO_VERSION"
    "$DNF" -y install "$MAIN_PKG-$TO_VERSION" || { echo "dnf failed, nothing changed"; exit 2; }
else
    echo "dnf: upgrading $PACKAGES"
    "$DNF" -y upgrade "${PKGS[@]}" || { echo "dnf failed, nothing changed"; exit 2; }
fi
AFTER="$(versions)"
echo "installed after:"; printf '%s\n' "$AFTER" | sed 's/^/  /'
AFTER_LINE="$(printf '%s' "$AFTER" | tr '\n' ' ')"
[ "$BEFORE" = "$AFTER" ] && echo "note: package versions unchanged (already latest)"

if [ "$SIMULATE" = 1 ]; then
    echo "simulate: appending '$BROKEN_LINE' to $BROKEN_FILE"
    printf '\n%s\n' "$BROKEN_LINE" >> "$BROKEN_FILE"
fi

echo "restarting $PRODUCT"
restart_ok=1
"$LABSVC" restart "$PRODUCT" || restart_ok=0
if [ "$restart_ok" = 1 ] && health; then
    echo "RESULT: upgrade of $PRODUCT OK ($AFTER_LINE)"
    exit 0
fi

echo "RESULT: $PRODUCT unhealthy after upgrade -> rolling back configuration"
"$LABSVC" stop "$PRODUCT" >/dev/null 2>&1 || true
restore_snapshot || { echo "RESULT: ROLLBACK FAILED (snapshot unusable) - escalate, see runbook 06"; exit 4; }
if "$LABSVC" restart "$PRODUCT" && health; then
    echo "RESULT: config rolled back, $PRODUCT healthy again; package still at: $AFTER_LINE"
    echo "hint: to downgrade the package too, use 'dnf history list $MAIN_PKG' and 'dnf history undo <id>'"
    exit 3
fi
echo "RESULT: ROLLBACK FAILED - $PRODUCT still unhealthy, escalate (runbook 06, vendor-questions.md)"
exit 4
