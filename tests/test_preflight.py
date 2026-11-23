import shutil

import pytest

ALMA = 'NAME="AlmaLinux"\nID="almalinux"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.6"\nPRETTY_NAME="AlmaLinux 9.6 (Sage Margay)"\n'
ROCKY = 'NAME="Rocky Linux"\nID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.6"\nPRETTY_NAME="Rocky Linux 9.6"\n'
EL8 = 'ID="almalinux"\nID_LIKE="rhel centos fedora"\nVERSION_ID="8.10"\nPRETTY_NAME="AlmaLinux 8.10"\n'
UBUNTU = 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'


@pytest.fixture
def pf(sandbox):
    """Sandbox prepared for preflight: good OS, dnf, 4 GB RAM, no ports."""
    sandbox.stub("dnf", "exit 0\n")
    osr = sandbox.tmp / "os-release"
    osr.write_text(ALMA)
    mem = sandbox.tmp / "meminfo"
    mem.write_text("MemTotal: 8000000 kB\nMemAvailable: 4194304 kB\n")
    sandbox.env.update({
        "LAB_OS_RELEASE_FILE": str(osr),
        "LAB_MEMINFO_FILE": str(mem),
        "LAB_PREFLIGHT_PATHS": str(sandbox.tmp),
        "LAB_MIN_DISK_MB": "1",
        "LAB_PORT_SOURCE": "proc",
        "LAB_PROC_NET_DIR": str(sandbox.proc_net()),
    })
    sandbox.osr = osr
    return sandbox


def test_all_checks_pass_on_almalinux(pf):
    r = pf.run("preflight.sh")
    assert r.returncode == 0, r.stdout
    assert "[OK]   OS release: AlmaLinux 9.6" in r.stdout
    assert "0 failed" in r.stdout


def test_rocky_is_accepted(pf):
    pf.osr.write_text(ROCKY)
    r = pf.run("preflight.sh")
    assert r.returncode == 0
    assert "Rocky Linux 9.6 (RHEL compatible)" in r.stdout


def test_non_rhel_os_fails(pf):
    pf.osr.write_text(UBUNTU)
    r = pf.run("preflight.sh")
    assert r.returncode == 1
    assert "[FAIL] OS release: Ubuntu 24.04 LTS is not RHEL compatible" in r.stdout


def test_el8_is_a_warning_not_a_failure(pf):
    pf.osr.write_text(EL8)
    r = pf.run("preflight.sh")
    assert r.returncode == 0
    assert "[WARN] OS release: AlmaLinux 8.10 is EL8" in r.stdout


@pytest.mark.skipif(shutil.which("dnf") is not None, reason="host has a real dnf")
def test_missing_dnf_fails(pf):
    (pf.bin / "dnf").unlink()
    r = pf.run("preflight.sh")
    assert r.returncode == 1
    assert "[FAIL] package manager: dnf not found" in r.stdout


def test_low_disk_fails_and_points_to_runbook(pf):
    r = pf.run("preflight.sh", "--min-disk-mb", "999999999")
    assert r.returncode == 1
    assert "need 999999999 MB" in r.stdout
    assert "runbook: docs/runbooks/03-disk-full.md" in r.stdout


def test_low_memory_fails(pf):
    r = pf.run("preflight.sh", "--min-mem-mb", "8192")
    assert r.returncode == 1
    assert "[FAIL] memory: 4096 MB available, need 8192 MB" in r.stdout


def test_port_in_use_fails_before_install(pf):
    pf.env["LAB_PROC_NET_DIR"] = str(pf.proc_net(listen_ports=[5432]))
    r = pf.run("preflight.sh", "--ports", "5432 5672")
    assert r.returncode == 1
    assert "[FAIL] port 5432: already in use" in r.stdout
    assert "runbook: docs/runbooks/02-port-in-use.md" in r.stdout
    assert "[OK]   port 5672: free" in r.stdout


def test_ports_mode_listening_after_install(pf):
    pf.env["LAB_PROC_NET_DIR"] = str(pf.proc_net(listen_ports=[5432, 5672]))
    r = pf.run("preflight.sh", "--ports", "5432 5672 15672", "--ports-mode", "listening")
    assert r.returncode == 1
    assert "[OK]   port 5432: listening" in r.stdout
    assert "[FAIL] port 15672: nothing listening" in r.stdout


def test_established_connections_are_not_listening(pf):
    pf.env["LAB_PROC_NET_DIR"] = str(pf.proc_net(listen_ports=[4369], other_ports=[5432]))
    r = pf.run("preflight.sh", "--ports", "5432 4369")
    assert "[OK]   port 5432: free" in r.stdout
    assert "[FAIL] port 4369: already in use" in r.stdout


def test_unknown_option_is_usage_error(pf):
    r = pf.run("preflight.sh", "--bogus")
    assert r.returncode == 64
