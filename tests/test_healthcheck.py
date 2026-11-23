import json
import subprocess

import pytest

from conftest import SCRIPTS

# curl stand-in for the RabbitMQ management API. Behaviour is steered by
# env vars: RMQ_STATUS (HTTP code for /overview), RMQ_BACKLOG (messages in
# configured queues). Published messages are kept in a file so /get returns
# exactly what was published.
CURL_STUB = r'''
import json, os, sys
args = sys.argv[1:]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
data = args[args.index("--data") + 1] if "--data" in args else ""
url = args[-1]
path = url.split("/api", 1)[1]
store = os.environ["RMQ_STORE"]
with open(os.environ["CALLS"], "a") as fh:
    fh.write(f"curl {method} {path}\n")

def reply(body, code):
    sys.stdout.write((json.dumps(body) if body is not None else "") + "\n" + str(code))
    sys.exit(0)

status = int(os.environ.get("RMQ_STATUS", "200"))
if status != 200:
    reply({"error": "not_authorised"}, status)
if path == "/overview":
    reply({"rabbitmq_version": "4.2.1"}, 200)
if method == "PUT" and path.startswith("/queues/"):
    reply(None, 201)
if path.endswith("/publish"):
    payload = json.loads(data)["payload"]
    if os.environ.get("RMQ_CORRUPT") == "1":
        payload = "something-else"
    open(store, "w").write(payload)
    reply({"routed": True}, 200)
if path.endswith("/get"):
    payload = open(store).read() if os.path.exists(store) else None
    reply([{"payload": payload}] if payload else [], 200)
if method == "GET" and path.startswith("/queues/"):
    reply({"messages": int(os.environ.get("RMQ_BACKLOG", "0"))}, 200)
reply({"error": "not found"}, 404)
'''


@pytest.fixture
def hc(sandbox):
    labsvc = sandbox.stub("labsvc", 'exit "${SVC_RC:-0}"\n')
    sandbox.stub("psql", """
        if [ "$PGPASSWORD" != "secret" ]; then
            echo 'psql: error: FATAL:  password authentication failed for user "app_user"' >&2; exit 2
        fi
        echo 'result=2;version=16.8'
    """)
    sandbox.stub("curl", CURL_STUB, interpreter="/usr/bin/env python3")
    sandbox.env.update({
        "LABSVC": str(labsvc),
        "LAB_PORT_SOURCE": "proc",
        "LAB_PROC_NET_DIR": str(sandbox.proc_net(listen_ports=[5432, 5672, 15672])),
        "RMQ_STORE": str(sandbox.tmp / "rmq-store"),
    })
    (sandbox.env_dir / "postgresql.env").write_text(
        "# written by ansible\nLAB_PG_USER=app_user\nLAB_PG_APP_PASSWORD=secret\n")
    (sandbox.env_dir / "rabbitmq.env").write_text(
        "LAB_RMQ_USER=lab_admin\nLAB_RMQ_PASSWORD=pw\nLAB_RMQ_QUEUES=orders.incoming orders.dlq\n")
    return sandbox


def test_postgresql_healthy(hc):
    r = hc.run("healthcheck.sh", "--product", "postgresql")
    assert r.returncode == 0, r.stdout
    assert "[OK]   postgresql test query as app_user@app_db: version=16.8" in r.stdout


def test_postgresql_wrong_password_points_to_credentials_runbook(hc):
    r = hc.run("healthcheck.sh", "--product", "postgresql", env={"LAB_PG_APP_PASSWORD": "wrong"})
    assert r.returncode == 1
    assert "password authentication failed" in r.stdout
    assert "04-wrong-credentials.md" in r.stdout


def test_postgresql_service_down(hc):
    hc.env["LAB_PROC_NET_DIR"] = str(hc.proc_net(listen_ports=[5672, 15672]))
    r = hc.run("healthcheck.sh", "--product", "postgresql", env={"SVC_RC": "3"})
    assert r.returncode == 1
    assert "[FAIL] postgresql service not running" in r.stdout
    assert "[FAIL] postgresql port 5432 not listening" in r.stdout
    assert "01-service-does-not-start.md" in r.stdout


def test_missing_password_is_reported(hc):
    (hc.env_dir / "postgresql.env").write_text("LAB_PG_USER=app_user\n")
    r = hc.run("healthcheck.sh", "--product", "postgresql")
    assert r.returncode == 1
    assert "LAB_PG_APP_PASSWORD not set" in r.stdout


def test_rabbitmq_smoke_test_round_trip(hc):
    r = hc.run("healthcheck.sh", "--product", "rabbitmq")
    assert r.returncode == 0, r.stdout
    assert "management API: version 4.2.1" in r.stdout
    assert "[OK]   smoke: message consumed, payload matches" in r.stdout
    calls = hc.calls_text()
    assert "curl PUT /queues/lab/lab.healthcheck" in calls
    assert "curl POST /exchanges/lab/amq.default/publish" in calls


def test_rabbitmq_payload_mismatch_fails(hc):
    r = hc.run("healthcheck.sh", "--product", "rabbitmq", env={"RMQ_CORRUPT": "1"})
    assert r.returncode == 1
    assert "does not match" in r.stdout


def test_rabbitmq_401_points_to_credentials_runbook(hc):
    r = hc.run("healthcheck.sh", "--product", "rabbitmq", env={"RMQ_STATUS": "401"})
    assert r.returncode == 1
    assert "HTTP 401 for user lab_admin" in r.stdout
    assert "04-wrong-credentials.md" in r.stdout


def test_queue_backlog_is_a_warning(hc):
    r = hc.run("healthcheck.sh", "--product", "rabbitmq", "--no-smoke",
               env={"RMQ_BACKLOG": "5000"})
    assert r.returncode == 0
    assert "[WARN] queue orders.incoming backlog: 5000 messages" in r.stdout
    assert "05-queue-backlog.md" in r.stdout
    assert "smoke" not in r.stdout


def test_environment_overrides_env_files(sandbox):
    (sandbox.env_dir / "a.env").write_text("FOO=from-file\nBAR=from-file\n# comment\nbad-key=x\n")
    script = '. "$LIB"; load_lab_env; echo "$FOO $BAR"'
    env = dict(sandbox.env, LIB=str(SCRIPTS / "lib/common.sh"), FOO="from-env")
    out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True).stdout
    assert out.strip() == "from-env from-file"


def test_invalid_product_is_usage_error(hc):
    assert hc.run("healthcheck.sh", "--product", "mysql").returncode == 64


def test_json_stub_sanity():
    # guard: the stub above must stay valid Python
    compile(CURL_STUB, "curl_stub", "exec")
    assert json.loads('{"a": 1}')["a"] == 1
