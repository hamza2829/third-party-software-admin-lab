import pytest


@pytest.fixture
def up(sandbox):
    """upgrade.sh with stubbed dnf/rpm/labsvc/healthcheck and a fake config dir.

    HC_RESULTS (file) holds the exit codes the healthcheck returns, one per
    call, e.g. "1 0" = fail after upgrade, pass after rollback.
    """
    version = sandbox.tmp / "version"
    version.write_text("4.1.4-1.el8")
    sandbox.stub("dnf", f"""
        [ "${{DNF_FAIL:-0}}" = 1 ] && exit 1
        case "$1" in -y) echo 4.2.1-1.el8 > {version} ;; esac
        exit "${{DNF_RC:-0}}"
    """)
    sandbox.stub("rpm", f"cat {version}\n")
    labsvc = sandbox.stub("labsvc", "exit 0\n")
    results = sandbox.tmp / "hc-results"
    results.write_text("0")
    hc = sandbox.stub("healthcheck", f"""
        set -- $(cat {results}); rc="${{1:-0}}"; shift || true; echo "$*" > {results}
        exit "$rc"
    """)
    conf = sandbox.tmp / "etc-rabbitmq"
    conf.mkdir()
    (conf / "rabbitmq.conf").write_text("listeners.tcp.default = 5672\n")
    sandbox.env.update({
        "LAB_DNF": "dnf", "LAB_RPM": "rpm", "LABSVC": str(labsvc), "LAB_HEALTHCHECK": str(hc),
        "LAB_SNAPSHOT_DIR": str(sandbox.tmp / "snapshots"),
        "LAB_UPGRADE_CONFIG_PATHS": str(conf),
        "LAB_UPGRADE_BROKEN_FILE": str(conf / "rabbitmq.conf"),
    })
    sandbox.conf = conf
    sandbox.results = results
    return sandbox


def snapshots(sb):
    return sorted((sb.tmp / "snapshots").glob("*.tar.gz"))


def test_successful_upgrade(up):
    r = up.run("upgrade.sh", "rabbitmq")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rabbitmq-server=4.1.4-1.el8" in r.stdout
    assert "rabbitmq-server=4.2.1-1.el8" in r.stdout
    assert "RESULT: upgrade of rabbitmq OK" in r.stdout
    assert len(snapshots(up)) == 1
    assert "dnf -y upgrade rabbitmq-server" in up.calls_text()
    assert "labsvc restart rabbitmq" in up.calls_text()


def test_snapshot_has_checksum_and_versions(up):
    up.run("upgrade.sh", "rabbitmq", check=True)
    snap = snapshots(up)[0]
    assert snap.with_name(snap.name + ".sha256").exists()
    versions = snap.with_name(snap.name.replace(".tar.gz", ".versions")).read_text()
    assert versions.strip() == "rabbitmq-server=4.1.4-1.el8"


def test_broken_config_is_rolled_back(up):
    up.results.write_text("1 0")  # unhealthy after upgrade, healthy after rollback
    r = up.run("upgrade.sh", "--simulate-broken-config", "rabbitmq")
    assert r.returncode == 3, r.stdout
    assert "config restored" in r.stdout
    assert (up.conf / "rabbitmq.conf").read_text() == "listeners.tcp.default = 5672\n"
    assert "dnf history undo" in r.stdout


def test_rollback_removes_files_added_by_upgrade(up):
    up.results.write_text("1 0")
    up.stub("dnf", f"touch {up.conf}/added-by-rpm.conf\n")
    r = up.run("upgrade.sh", "rabbitmq")
    assert r.returncode == 3
    assert not (up.conf / "added-by-rpm.conf").exists()


def test_failed_rollback_escalates(up):
    up.results.write_text("1 1")
    r = up.run("upgrade.sh", "rabbitmq")
    assert r.returncode == 4
    assert "ROLLBACK FAILED" in r.stdout


def test_dnf_failure_changes_nothing(up):
    r = up.run("upgrade.sh", "rabbitmq", env={"DNF_FAIL": "1"})
    assert r.returncode == 2
    assert "labsvc restart" not in up.calls_text()


def test_dry_run_does_not_touch_anything(up):
    r = up.run("upgrade.sh", "--dry-run", "rabbitmq", env={"DNF_RC": "100"})
    assert r.returncode == 0
    assert "updates available" in r.stdout
    assert snapshots(up) == []
    assert "dnf -q check-update rabbitmq-server" in up.calls_text()
    assert "-y" not in up.calls_text()


def test_to_version_installs_exact_version(up):
    r = up.run("upgrade.sh", "--to", "4.2.0", "rabbitmq")
    assert r.returncode == 0
    assert "dnf -y install rabbitmq-server-4.2.0" in up.calls_text()


def test_unknown_product_is_usage_error(up):
    assert up.run("upgrade.sh", "nginx").returncode == 64
