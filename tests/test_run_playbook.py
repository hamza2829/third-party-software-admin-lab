import pytest

RECAP = """
PLAY [Third-party software admin lab] ******************************************

PLAY RECAP *********************************************************************
localhost                  : ok=41   changed={changed}    unreachable=0    failed={failed}    skipped=2    rescued=0    ignored=0
"""


@pytest.mark.parametrize("changed,failed,assert_idem,rc", [
    (0, 0, True, 0),     # second run, idempotent
    (3, 0, True, 2),     # second run changed something -> not idempotent
    (27, 0, False, 0),   # first run may change things
    (0, 1, False, 1),    # failures always fail
])
def test_recap_evaluation(sandbox, changed, failed, assert_idem, rc):
    log = sandbox.tmp / "run.log"
    log.write_text(RECAP.format(changed=changed, failed=failed))
    args = ["--check-log", str(log)] + (["--assert-idempotent"] if assert_idem else [])
    r = sandbox.run("run_playbook.sh", *args)
    assert r.returncode == rc, r.stdout
    assert f"changed={changed} failed={failed}" in r.stdout


def test_log_without_recap_fails(sandbox):
    log = sandbox.tmp / "run.log"
    log.write_text("ERROR! the playbook could not be found\n")
    assert sandbox.run("run_playbook.sh", "--check-log", str(log)).returncode == 1
