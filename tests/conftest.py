"""Shared fixtures: run the bash scripts against stub commands.

No real PostgreSQL, RabbitMQ or dnf is needed. Each stub is a small script
placed first in PATH; most stubs append their arguments to calls.log so tests
can assert what the script under test executed.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


class Sandbox:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.calls = tmp / "calls.log"
        self.calls.touch()
        self.env_dir = tmp / "etc-lab"
        self.env_dir.mkdir()
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(tmp),
            "LAB_ENV_DIR": str(self.env_dir),
            "LAB_LOG_DIR": str(tmp / "log"),
            "CALLS": str(self.calls),
            "LANG": "C",
        }

    def stub(self, name: str, body: str, interpreter: str = "/usr/bin/env bash") -> Path:
        """Create an executable stub. bash stubs log '<name> <args>' first."""
        path = self.bin / name
        if "bash" in interpreter:
            header = f'#!{interpreter}\necho "{name} $*" >> "$CALLS"\n'
        else:
            header = f"#!{interpreter}\n"
        path.write_text(header + textwrap.dedent(body))
        path.chmod(0o755)
        return path

    def proc_net(self, listen_ports=(), other_ports=()) -> Path:
        """Fake /proc/net with tcp + tcp6 files; returns the directory."""
        d = self.tmp / "proc-net"
        d.mkdir(exist_ok=True)
        header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        rows = []
        for i, port in enumerate(listen_ports):
            rows.append(f"   {i}: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1 1\n")
        for i, port in enumerate(other_ports):
            rows.append(f"   {i}: 0100007F:{port:04X} 0100007F:D431 01 00000000:00000000 00:00000000 00000000 0 0 1 1\n")
        (d / "tcp").write_text(header + "".join(rows))
        (d / "tcp6").write_text(header)
        return d

    def calls_text(self) -> str:
        return self.calls.read_text()

    def run(self, script: str, *args: str, env: dict | None = None, check: bool = False):
        full_env = dict(self.env)
        full_env.update(env or {})
        cmd = [str(SCRIPTS / script), *args]
        result = subprocess.run(cmd, env=full_env, capture_output=True, text=True, timeout=60)
        if check and result.returncode != 0:
            raise AssertionError(f"{cmd} failed rc={result.returncode}\n{result.stdout}\n{result.stderr}")
        return result


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)
