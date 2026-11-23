import pytest


@pytest.fixture
def svc(sandbox):
    """pg_ctl stub whose state lives in a file; direct (non-systemd) mode."""
    state = sandbox.tmp / "pg.running"
    sandbox.stub("pg_ctl", f"""
        case "$*" in
            *status*) [ -f {state} ] ;;
            *start*) touch {state} ;;
            *stop*) rm -f {state} ;;
        esac
    """)
    sandbox.env.update({"LAB_SERVICE_MANAGER": "direct", "LAB_PG_RUN_AS": ""})
    sandbox.state = state
    return sandbox


def test_status_stopped_exits_3(svc):
    r = svc.run("svc.sh", "status", "postgresql")
    assert r.returncode == 3
    assert "postgresql: stopped" in r.stdout


def test_ensure_started_starts_a_stopped_service(svc):
    r = svc.run("svc.sh", "ensure-started", "postgresql")
    assert r.returncode == 0, r.stderr
    assert "STATE=started" in r.stdout
    assert "start" in svc.calls_text()
    assert svc.state.exists()


def test_ensure_started_is_a_noop_when_running(svc):
    svc.state.touch()
    r = svc.run("svc.sh", "ensure-started", "postgresql")
    assert "STATE=running" in r.stdout
    calls = svc.calls_text().splitlines()
    assert calls == [l for l in calls if "status" in l]  # only status was called


def test_restart_stops_then_starts(svc):
    svc.state.touch()
    r = svc.run("svc.sh", "restart", "postgresql")
    assert r.returncode == 0
    lines = svc.calls_text()
    assert lines.index("stop") < lines.rindex("start")


def test_systemd_mode_uses_unit_names(sandbox):
    sandbox.stub("systemctl", "exit 0\n")
    r = sandbox.run("svc.sh", "restart", "rabbitmq", env={"LAB_SERVICE_MANAGER": "systemd"})
    assert r.returncode == 0
    assert "systemctl restart rabbitmq-server" in sandbox.calls_text()


def test_unknown_service_is_usage_error(sandbox):
    r = sandbox.run("svc.sh", "start", "mysql")
    assert r.returncode == 64
