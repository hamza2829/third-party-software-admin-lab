"""Render the role templates with role defaults and check the result.

These run without Ansible or a target host; they catch broken Jinja,
invalid XML and access rules in the wrong order.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

jinja2 = pytest.importorskip("jinja2")

ROLES = Path(__file__).resolve().parent.parent / "ansible" / "roles"


def render(role: str, template: str, **extra) -> str:
    defaults = yaml.safe_load((ROLES / role / "defaults/main.yml").read_text())
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROLES / role / "templates"),
                             undefined=jinja2.StrictUndefined)
    ctx = {"ansible_managed": "Ansible managed", **defaults, **extra}
    return env.get_template(template).render(**ctx)


def test_firewalld_service_xml_is_valid():
    defaults = yaml.safe_load((ROLES / "common/defaults/main.yml").read_text())
    for svc in defaults["common_firewall_services"]:
        root = ET.fromstring(render("common", "firewalld-service.xml.j2", item=svc).encode())
        ports = {(p.get("port"), p.get("protocol")) for p in root.findall("port")}
        assert ports == {(str(p["port"]), p["protocol"]) for p in svc["ports"]}


def test_firewalld_zone_lists_sources_and_services():
    root = ET.fromstring(render("common", "firewalld-zone.xml.j2").encode())
    assert {s.get("address") for s in root.findall("source")} == {"10.0.0.0/8", "192.168.0.0/16"}
    assert {"lab-postgresql", "lab-rabbitmq", "ssh"} <= {s.get("name") for s in root.findall("service")}


def test_logrotate_config():
    text = render("common", "logrotate-lab.j2")
    assert text.splitlines()[1].startswith("/var/log/lab/*.log /var/log/lab/postgresql/*.log {")
    assert "copytruncate" in text and "rotate 8" in text


def test_pg_hba_allows_app_then_rejects_everything_else():
    text = render("postgresql", "pg_hba.conf.j2", postgresql_password="x")
    rules = [line.split() for line in text.splitlines() if line and not line.startswith("#")]
    assert rules[0] == ["local", "all", "postgres", "peer", "map=lab_admin"]
    assert ["host", "app_db", "app_user", "127.0.0.1/32", "scram-sha-256"] in rules
    assert all(r[-1] == "reject" for r in rules[-3:])
    assert not any(r[-1] in ("trust", "md5", "password") for r in rules)


def test_rabbitmq_env_file_lists_queues():
    text = render("rabbitmq", "rabbitmq.env.j2", rabbitmq_admin_password="x")
    assert "LAB_RMQ_QUEUES=orders.incoming orders.dlq notifications.email" in text
    assert render("rabbitmq", "enabled_plugins.j2").strip() == "[rabbitmq_management]."
