#!/usr/bin/env bash
# run_playbook.sh - run the lab playbook and evaluate the PLAY RECAP.
#
#   run_playbook.sh [--assert-idempotent] [--log FILE] [-- extra ansible-playbook args]
#   run_playbook.sh --check-log FILE [--assert-idempotent]   (only evaluate a log)
#
# --assert-idempotent fails (exit 2) unless every host reports changed=0.
# Any failed/unreachable host exits 1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ASSERT=0; LOG=""; CHECK_ONLY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --assert-idempotent) ASSERT=1; shift ;;
        --log) LOG="$2"; shift 2 ;;
        --check-log) CHECK_ONLY="$2"; shift 2 ;;
        --) shift; break ;;
        *) echo "unknown option: $1" >&2; exit 64 ;;
    esac
done

# Prints "changed=<sum> failed=<sum> unreachable=<sum> hosts=<n>" from a log.
recap_totals() {
    awk '
        /^PLAY RECAP/ { inrecap = 1; next }
        inrecap && /ok=[0-9]+/ {
            hosts++
            for (i = 1; i <= NF; i++) {
                split($i, kv, "=")
                if (kv[1] == "changed") c += kv[2]
                if (kv[1] == "failed") f += kv[2]
                if (kv[1] == "unreachable") u += kv[2]
            }
        }
        END { printf "changed=%d failed=%d unreachable=%d hosts=%d\n", c, f, u, hosts }
    ' "$1"
}

evaluate() {
    local totals changed failed unreachable hosts
    totals="$(recap_totals "$1")"
    echo "recap: $totals"
    read -r changed failed unreachable hosts <<< "$(printf '%s' "$totals" | sed 's/[a-z]*=//g')"
    [ "$hosts" -gt 0 ] || { echo "no PLAY RECAP found"; return 1; }
    [ "$failed" -eq 0 ] && [ "$unreachable" -eq 0 ] || { echo "playbook had failures"; return 1; }
    if [ "$ASSERT" = 1 ] && [ "$changed" -ne 0 ]; then
        echo "NOT IDEMPOTENT: $changed task(s) changed on a repeated run"
        return 2
    fi
    [ "$ASSERT" = 1 ] && echo "idempotent: 0 changes"
    return 0
}

if [ -n "$CHECK_ONLY" ]; then
    evaluate "$CHECK_ONLY"; exit $?
fi

LOG="${LOG:-$(mktemp)}"
cd "$REPO_DIR/ansible"
export ANSIBLE_CONFIG="$REPO_DIR/ansible/ansible.cfg"
set +e
ansible-playbook site.yml "$@" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
set -e
evaluate "$LOG" || exit $?
exit "$rc"
