import pytest


@pytest.fixture
def br(sandbox):
    """Stubs for pg_dump/pg_restore/psql/createdb/dropdb.

    DB_EXISTS=1 makes the target database exist, PG_DUMP_FAIL=1 breaks
    pg_dump, COUNT_<db> sets the row count reported for that database.
    """
    sandbox.stub("pg_dump", """
        [ "${PG_DUMP_FAIL:-0}" = 1 ] && { echo 'pg_dump: error: connection failed' >&2; printf 'x'; exit 1; }
        printf 'PGDMP-fake-custom-format-dump'
    """)
    sandbox.stub("pg_restore", "cat > /dev/null\n")
    sandbox.stub("createdb", "exit 0\n")
    sandbox.stub("dropdb", "exit 0\n")
    sandbox.stub("psql", r"""
        sql="${*: -1}"
        db=""; prev=""
        for a in "$@"; do [ "$prev" = "-d" ] && db="$a"; prev="$a"; done
        case "$sql" in
            *pg_database*) echo "${DB_EXISTS:-0}" ;;
            *pg_tables*) printf 'public.orders\npublic.customers\n' ;;
            *"count(*) from public.orders"*) case "$db" in app_db) echo "${COUNT_SRC:-42}" ;; *) echo "${COUNT_DST:-42}" ;; esac ;;
            *"count(*) from"*) echo 7 ;;
        esac
    """)
    backups = sandbox.tmp / "backups"
    sandbox.env.update({"LAB_BACKUP_DIR": str(backups), "LAB_BACKUP_KEEP": "3"})
    sandbox.backups = backups
    return sandbox


def dumps(sb):
    return sorted(p.name for p in sb.backups.glob("*.dump"))


def test_backup_writes_dump_and_checksum(br):
    r = br.run("backup_restore.sh", "backup", check=True)
    files = dumps(br)
    assert len(files) == 1 and files[0].startswith("app_db-")
    assert (br.backups / (files[0] + ".sha256")).exists()
    assert "backup written" in r.stdout
    assert "pg_dump -U postgres -Fc -d app_db" in br.calls_text()


def test_failed_dump_leaves_no_partial_file(br):
    r = br.run("backup_restore.sh", "backup", env={"PG_DUMP_FAIL": "1"})
    assert r.returncode == 1
    assert list(br.backups.iterdir()) == []


def test_prune_keeps_newest(br):
    br.backups.mkdir()
    for i in range(5):
        (br.backups / f"app_db-2026010{i}T000000Z.dump").write_text("x")
        (br.backups / f"app_db-2026010{i}T000000Z.dump.sha256").write_text("x")
    r = br.run("backup_restore.sh", "prune", "--keep", "2", check=True)
    assert dumps(br) == ["app_db-20260103T000000Z.dump", "app_db-20260104T000000Z.dump"]
    assert r.stdout.count("pruned:") == 3
    assert len(list(br.backups.glob("*.sha256"))) == 2


def test_restore_rejects_damaged_file(br):
    br.run("backup_restore.sh", "backup", check=True)
    dump = br.backups / dumps(br)[0]
    dump.write_text("tampered")
    r = br.run("backup_restore.sh", "restore", str(dump), "--target-db", "restored")
    assert r.returncode == 3
    assert "checksum mismatch" in r.stderr
    assert "pg_restore" not in br.calls_text()


def test_restore_refuses_to_overwrite_without_force(br):
    br.run("backup_restore.sh", "backup", check=True)
    dump = br.backups / dumps(br)[0]
    r = br.run("backup_restore.sh", "restore", str(dump), env={"DB_EXISTS": "1"})
    assert r.returncode == 4
    assert "use --force" in r.stderr


def test_restore_with_force_drops_and_recreates(br):
    br.run("backup_restore.sh", "backup", check=True)
    dump = br.backups / dumps(br)[0]
    r = br.run("backup_restore.sh", "restore", str(dump), "--force", env={"DB_EXISTS": "1"}, check=True)
    calls = br.calls_text()
    assert "dropdb -U postgres --if-exists app_db" in calls
    assert "createdb -U postgres -O app_user app_db" in calls
    assert calls.index("dropdb") < calls.index("createdb") < calls.index("pg_restore")
    assert "restored" in r.stdout


def test_verify_restores_into_temp_db_and_compares(br):
    br.run("backup_restore.sh", "backup", check=True)
    r = br.run("backup_restore.sh", "verify", check=True)
    assert "verify ok" in r.stdout
    assert "public.orders=42" in r.stdout
    calls = br.calls_text()
    assert "createdb -U postgres -O app_user app_db_verify_" in calls
    assert "dropdb -U postgres --if-exists app_db_verify_" in calls  # cleaned up


def test_verify_detects_row_count_difference(br):
    br.run("backup_restore.sh", "backup", check=True)
    r = br.run("backup_restore.sh", "verify", env={"COUNT_SRC": "43"})
    assert r.returncode == 5
    assert "row counts differ" in r.stdout


def test_verify_without_backups_fails(br):
    r = br.run("backup_restore.sh", "verify")
    assert r.returncode == 2
    assert "no backup found" in r.stderr
