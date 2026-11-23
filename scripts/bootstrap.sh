#!/usr/bin/env bash
# bootstrap.sh - prepare an AlmaLinux/Rocky 9 host (or container) to run the
# lab: base tools, Python 3.11 and a virtualenv with Ansible.
#
#   sudo scripts/bootstrap.sh            # venv in /opt/lab/venv
#
# Ansible comes from the `ansible` PyPI package (ansible-core + the
# community.postgresql and community.rabbitmq collections), so no Galaxy
# access is needed.
set -euo pipefail

VENV="${LAB_VENV:-/opt/lab/venv}"
ANSIBLE_SPEC="${LAB_ANSIBLE_SPEC:-ansible>=11,<12}"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

dnf -y -q install python3.11 python3.11-pip tar gzip procps-ng iproute findutils \
    hostname which util-linux curl-minimal --setopt=install_weak_deps=False --allowerasing

if [ ! -x "$VENV/bin/python" ]; then
    python3.11 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "$ANSIBLE_SPEC" psycopg2-binary requests
"$VENV/bin/ansible" --version | head -n 3
echo "bootstrap done: source $VENV/bin/activate"
