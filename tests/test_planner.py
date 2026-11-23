import copy
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import maintenance_planner as mp  # noqa: E402

START = dt.date(2026, 3, 2)  # a Monday


def product(name, weekday="saturday", depends_on=(), departments=("Ops",), crit="medium",
            version="1.0.0", target="1.0.1"):
    return {
        "name": name, "version": version, "target_version": target,
        "owner": {"name": "Owner " + name, "email": f"{name.lower()}@example.org"},
        "departments": list(departments), "criticality": crit,
        "window": {"weekday": weekday, "start": "06:00", "duration_minutes": 60},
        "depends_on": list(depends_on),
    }


BASE = {
    "planning": {"timezone": "UTC", "lead_time_days": 7, "max_changes_per_day": 1, "blackouts": []},
    "departments": {"Ops": {"contact": "ops@example.org"}, "Finance": {"contact": "fin@example.org"}},
    "products": [],
}


def plan(products, **planning):
    data = copy.deepcopy(BASE)
    data["products"] = products
    data["planning"].update(planning)
    inv = mp.parse_inventory(data)
    return inv, mp.build_plan(inv, START)


def test_lead_time_and_weekday_are_respected():
    _, windows = plan([product("A", weekday="tuesday")])
    w = windows[0]
    assert w.day.weekday() == 1
    assert w.day >= START + dt.timedelta(days=7)
    assert w.day == dt.date(2026, 3, 10)
    assert w.notify_by == dt.date(2026, 3, 3)


def test_dependencies_are_scheduled_first():
    _, windows = plan([product("App", depends_on=["DB"], crit="low"), product("DB", crit="high")])
    order = [w.product.name for w in windows]
    assert order == ["DB", "App"]
    assert windows[1].day > windows[0].day


def test_low_criticality_goes_first_when_independent():
    _, windows = plan([product("Core", crit="high", weekday="monday"),
                       product("Tool", crit="low", weekday="monday")])
    assert [w.product.name for w in windows] == ["Tool", "Core"]


def test_max_changes_per_day():
    _, windows = plan([product("A"), product("B")])
    assert windows[0].day != windows[1].day
    _, windows = plan([product("A"), product("B")], max_changes_per_day=2)
    assert windows[0].day == windows[1].day


def test_global_blackout_is_skipped():
    _, windows = plan([product("A")], blackouts=[
        {"from": "2026-03-14", "to": "2026-03-21", "reason": "freeze"}])
    assert windows[0].day == dt.date(2026, 3, 28)


def test_department_month_end_freeze():
    data = copy.deepcopy(BASE)
    data["departments"]["Finance"]["month_end_freeze_days"] = 5
    data["products"] = [product("Ledger", departments=["Finance"])]
    inv = mp.parse_inventory(data)
    # first Saturday after lead time is 2026-03-28, inside the last 5 days of March
    w = mp.build_plan(inv, dt.date(2026, 3, 20))[0]
    assert w.day == dt.date(2026, 4, 4)
    assert mp.blocked_reason(dt.date(2026, 3, 28), inv.products[0], inv) == "Finance month-end freeze"


def test_month_end_freeze_handles_february():
    assert mp.in_month_end_freeze(dt.date(2026, 2, 26), 3)
    assert not mp.in_month_end_freeze(dt.date(2026, 2, 25), 3)
    assert not mp.in_month_end_freeze(dt.date(2026, 2, 28), 0)


@pytest.mark.parametrize("current,target,expected", [
    ("16.4", "16.8", "minor"),
    ("3.8.2", "3.8.5", "patch"),
    ("5.2.0", "6.0.1", "major"),
    ("27.3.4", "27.3.4.3", "patch"),
    ("2024.4", "2025.1", "major"),
    ("1.0", "1.0.0", "none"),
])
def test_change_type(current, target, expected):
    assert mp.change_type(current, target) == expected


def test_risk_level_matrix():
    assert mp.risk_level("high", "major") == "high"
    assert mp.risk_level("high", "patch") == "medium"
    assert mp.risk_level("low", "major") == "medium"
    assert mp.risk_level("low", "patch") == "low"


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["products"][0].update(departments=["Sales"]), "unknown department"),
    (lambda d: d["products"][0].update(depends_on=["Ghost"]), "unknown dependency"),
    (lambda d: d["products"][0].update(target_version="0.9"), "lower than"),
    (lambda d: d["products"][0]["window"].update(weekday="someday"), "invalid weekday"),
    (lambda d: d["products"][0].update(criticality="extreme"), "criticality"),
    (lambda d: d["products"].append(product("A")), "duplicate product"),
    (lambda d: d["planning"].update(blackouts=[{"from": "2026-03-10", "to": "2026-03-01"}]), "before"),
])
def test_invalid_inventory_is_rejected(mutate, message):
    data = copy.deepcopy(BASE)
    data["products"] = [product("A")]
    mutate(data)
    with pytest.raises(mp.PlannerError, match=message):
        mp.parse_inventory(data)


def test_dependency_cycle_is_rejected():
    data = copy.deepcopy(BASE)
    data["products"] = [product("A", depends_on=["B"]), product("B", depends_on=["A"])]
    inv = mp.parse_inventory(data)
    with pytest.raises(mp.PlannerError, match="cycle"):
        mp.build_plan(inv, START)


def test_department_message_lists_all_windows_and_contacts():
    inv, windows = plan([product("A"), product("B", departments=["Ops", "Finance"], crit="high",
                                                 version="1.0", target="2.0")])
    text = mp.department_message("Ops", windows, inv)
    assert text.startswith("Subject: Planned maintenance: 2 windows")
    assert "Dear Ops team," in text
    assert "A 1.0.0 → 1.0.1 (patch update)" in text
    assert "B 1.0 → 2.0 (major update)" in text
    assert "high risk" in text
    assert "Owner A <a@example.org>; Owner B <b@example.org>" in text


def test_markdown_has_schedule_and_one_section_per_department():
    inv, windows = plan([product("A")])
    md = mp.render_markdown(windows, inv, START)
    assert "| 1 | Sat 2026-03-14 | 06:00–07:00 | A |" in md
    assert "### Ops" in md and "### Finance" in md
    assert "No maintenance affecting this department" in md  # Finance


def test_cli_json_output(tmp_path, capsys):
    inv_file = tmp_path / "inv.yml"
    data = copy.deepcopy(BASE)
    data["products"] = [product("A")]
    inv_file.write_text(yaml.safe_dump(data))
    assert mp.main(["--inventory", str(inv_file), "--start", "2026-03-02", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["product"] == "A" and out[0]["date"] == "2026-03-14"


def test_cli_reports_errors_with_exit_2(tmp_path, capsys):
    assert mp.main(["--inventory", str(tmp_path / "missing.yml")]) == 2
    assert "inventory not found" in capsys.readouterr().err


def test_repository_inventory_plan_is_up_to_date():
    """docs/maintenance-plan.md must match what the planner generates (start 2026-12-01)."""
    inv = mp.load_inventory(REPO / "inventory.yml")
    windows = mp.build_plan(inv, dt.date(2026, 12, 1))
    expected = mp.render_markdown(windows, inv, dt.date(2026, 12, 1))
    assert (REPO / "docs/maintenance-plan.md").read_text(encoding="utf-8") == expected
    names = [w.product.name for w in windows]
    assert names.index("Erlang/OTP") < names.index("RabbitMQ") < names.index("OrderDesk ERP connector")
    for w in windows:
        assert not any(b.covers(w.day) for b in inv.blackouts)
