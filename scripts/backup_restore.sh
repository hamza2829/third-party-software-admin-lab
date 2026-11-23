#!/usr/bin/env bash
# backup_restore.sh - PostgreSQL backup, restore and restore test.
#
#   backup_restore.sh backup  [--db NAME]
#   backup_restore.sh list
#   backup_restore.sh verify  [FILE] [--db NAME]   restore into a temporary DB,
#                                                  compare row counts, drop it
#   backup_restore.sh restore FILE [--target-db NAME] [--force]
#   backup_restore.sh prune   [--keep N]
#
# Dumps use the custom format (pg_dump -Fc) and get a .sha256 file next to
# them; restore and verify refuse a file whose checksum does not match.
# Admin connections use the local socket as the postgres role; the roles map
# OS user root to it in pg_ident.conf (peer authentication, no password).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
load_lab_env

BACKUP_DIR="${LAB_BACKUP_DIR:-/var/backups/lab/postgresql}"
KEEP="${LAB_BACKUP_KEEP:-7}"
DB="${LAB_PG_DB:-app_db}"
OWNER="${LAB_PG_USER:-app_user}"
SUPERUSER="${LAB_PG_SUPERUSER:-postgres}"
PG_OPTS=(-U "$SUPERUSER")
[ -n "${LAB_PG_SOCKET_DIR:-}" ] && PG_OPTS+=(-h "$LAB_PG_SOCKET_DIR")

usage() { sed -n '2,14p' "$0" >&2; exit 64; }

pg_sql() {  # pg_sql DB SQL -> unaligned, tuples only
    psql -X -At -v ON_ERROR_STOP=1 "${PG_OPTS[@]}" -d "$1" -c "$2"
}

db_exists() {
    [ "$(pg_sql postgres "select count(*) from pg_database where datname = '$1'")" = 1 ]
}

latest_backup() {
    [ -d "$BACKUP_DIR" ] || return 0
    find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -type f | sort | tail -n 1
}

check_sum() {
    local f="$1"
    [ -f "$f" ] || die "backup file not found: $f" 2
    [ -f "$f.sha256" ] || die "checksum file missing: $f.sha256" 2
    (cd "$(dirname "$f")" && sha256sum -c --status "$(basename "$f").sha256") \
        || die "checksum mismatch for $f - file is damaged, do not restore it" 3
    echo "checksum ok: $(basename "$f")"
}

# "schema.table=rows" for every user table, sorted
table_counts() {
    local t
    pg_sql "$1" "select format('%I.%I', schemaname, tablename) from pg_tables
                 where schemaname not in ('pg_catalog', 'information_schema') order by 1" |
    while IFS= read -r t; do
        [ -n "$t" ] || continue
        printf '%s=%s\n' "$t" "$(pg_sql "$1" "select count(*) from $t")"
    done
}

cmd_backup() {
    while [ $# -gt 0 ]; do
        case "$1" in --db) DB="$2"; shift 2 ;; *) usage ;; esac
    done
    mkdir -p "$BACKUP_DIR"
    local file tmp
    file="$BACKUP_DIR/${DB}-$(date -u +%Y%m%dT%H%M%SZ).dump"
    tmp="$file.partial"
    if ! pg_dump "${PG_OPTS[@]}" -Fc -d "$DB" > "$tmp"; then
        rm -f "$tmp"; die "pg_dump of $DB failed" 1
    fi
    [ -s "$tmp" ] || { rm -f "$tmp"; die "pg_dump produced an empty file" 1; }
    mv "$tmp" "$file"
    (cd "$BACKUP_DIR" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256")
    chmod 0640 "$file" "$file.sha256"
    echo "backup written: $file ($(du -k "$file" | cut -f1) KiB)"
    cmd_prune
}

cmd_list() {
    local f
    for f in $(find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -type f 2>/dev/null | sort); do
        printf '%s\t%s KiB\n' "$(basename "$f")" "$(du -k "$f" | cut -f1)"
    done
}

cmd_prune() {
    while [ $# -gt 0 ]; do
        case "$1" in --keep) KEEP="$2"; shift 2 ;; *) usage ;; esac
    done
    local old
    find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -type f | sort | head -n "-$KEEP" | while IFS= read -r old; do
        rm -f "$old" "$old.sha256"
        echo "pruned: $(basename "$old")"
    done
}

cmd_restore() {
    local file="" target="" force=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --target-db) target="$2"; shift 2 ;;
            --force) force=1; shift ;;
            -*) usage ;;
            *) file="$1"; shift ;;
        esac
    done
    [ -n "$file" ] || usage
    target="${target:-$DB}"
    check_sum "$file"
    if db_exists "$target"; then
        [ "$force" = 1 ] || die "database $target exists; use --force to drop and replace it" 4
        echo "dropping existing database $target"
        dropdb "${PG_OPTS[@]}" --if-exists "$target"
    fi
    createdb "${PG_OPTS[@]}" -O "$OWNER" "$target"
    pg_restore "${PG_OPTS[@]}" --exit-on-error -d "$target" < "$file"
    echo "restored $(basename "$file") into $target"
}

cmd_verify() {
    local file=""
    while [ $# -gt 0 ]; do
        case "$1" in --db) DB="$2"; shift 2 ;; -*) usage ;; *) file="$1"; shift ;; esac
    done
    file="${file:-$(latest_backup)}"
    [ -n "$file" ] || die "no backup found in $BACKUP_DIR" 2
    check_sum "$file"
    local tmpdb="${DB}_verify_$$"
    # shellcheck disable=SC2064
    trap "dropdb ${PG_OPTS[*]} --if-exists $tmpdb >/dev/null 2>&1 || true" EXIT
    createdb "${PG_OPTS[@]}" -O "$OWNER" "$tmpdb"
    pg_restore "${PG_OPTS[@]}" --exit-on-error -d "$tmpdb" < "$file"
    local src dst
    src="$(table_counts "$DB")"
    dst="$(table_counts "$tmpdb")"
    echo "tables in backup: $(printf '%s\n' "$dst" | grep -c . || true)"
    printf '%s\n' "$dst" | sed 's/^/  /'
    if [ "$src" = "$dst" ]; then
        echo "verify ok: $(basename "$file") restores and matches $DB row counts"
    else
        echo "verify FAILED: row counts differ (expected if $DB changed since the backup)"
        diff <(printf '%s\n' "$src") <(printf '%s\n' "$dst") || true
        return 5
    fi
}

[ $# -ge 1 ] || usage
CMD="$1"; shift
case "$CMD" in
    backup) cmd_backup "$@" ;;
    list) cmd_list ;;
    prune) cmd_prune "$@" ;;
    restore) cmd_restore "$@" ;;
    verify) cmd_verify "$@" ;;
    *) usage ;;
esac
