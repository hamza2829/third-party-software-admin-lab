#!/usr/bin/env python3
"""Generate a maintenance window plan from inventory.yml.

Reads products (owner, versions, departments, preferred window, dependencies)
and planning rules (lead time, blackouts, department freezes) and writes a
Markdown plan with dates, affected departments and one communication text
per department.

    python tools/maintenance_planner.py --start 2026-12-01 \
        --inventory inventory.yml --output docs/maintenance-plan.md

Scheduling rules, in this order:
  * dependencies first (a product is updated at least one day after the
    products it depends on),
  * among products that are ready, lower criticality first (canary),
  * not before start + lead_time_days (departments must be informed),
  * only on the product's preferred weekday,
  * never in a global blackout or a department's month-end freeze,
  * at most max_changes_per_day changes on one day.

Requires Python 3.11+ and PyYAML.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import heapq
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
CRITICALITY = {"low": 0, "medium": 1, "high": 2}
SEARCH_HORIZON_DAYS = 366


class PlannerError(Exception):
    """Invalid inventory or impossible plan."""


@dataclass(frozen=True)
class Blackout:
    start: dt.date
    end: dt.date
    reason: str

    def covers(self, day: dt.date) -> bool:
        return self.start <= day <= self.end


@dataclass
class Product:
    name: str
    package: str
    vendor: str
    version: str
    target_version: str
    owner_name: str
    owner_email: str
    departments: list[str]
    criticality: str
    weekday: str
    start_time: dt.time
    duration_minutes: int
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Window:
    product: Product
    day: dt.date
    notify_by: dt.date

    @property
    def start(self) -> dt.datetime:
        return dt.datetime.combine(self.day, self.product.start_time)

    @property
    def end(self) -> dt.datetime:
        return self.start + dt.timedelta(minutes=self.product.duration_minutes)

    @property
    def change_type(self) -> str:
        return change_type(self.product.version, self.product.target_version)

    @property
    def risk(self) -> str:
        return risk_level(self.product.criticality, self.change_type)


@dataclass
class Inventory:
    timezone: str
    lead_time_days: int
    max_changes_per_day: int
    blackouts: list[Blackout]
    departments: dict[str, dict]
    products: list[Product]


# --------------------------------------------------------------------------
# parsing and validation
# --------------------------------------------------------------------------

def _date(value, what: str) -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise PlannerError(f"{what}: invalid date {value!r}") from exc


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for piece in str(version).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def change_type(current: str, target: str) -> str:
    """'major', 'minor' or 'patch' (anything below the second component)."""
    cur, tgt = _version_tuple(current), _version_tuple(target)
    width = max(len(cur), len(tgt), 2)
    cur += (0,) * (width - len(cur))
    tgt += (0,) * (width - len(tgt))
    if tgt < cur:
        raise PlannerError(f"target version {target} is lower than {current}")
    if tgt == cur:
        return "none"
    if tgt[0] != cur[0]:
        return "major"
    if tgt[1] != cur[1]:
        return "minor"
    return "patch"


def risk_level(criticality: str, change: str) -> str:
    score = CRITICALITY[criticality] + {"none": 0, "patch": 0, "minor": 1, "major": 2}[change]
    return "high" if score >= 3 else "medium" if score >= 2 else "low"


def parse_inventory(data: dict) -> Inventory:
    if not isinstance(data, dict):
        raise PlannerError("inventory must be a mapping")
    planning = data.get("planning") or {}
    departments = data.get("departments") or {}
    raw_products = data.get("products") or []
    if not raw_products:
        raise PlannerError("inventory has no products")

    blackouts = []
    for i, b in enumerate(planning.get("blackouts") or []):
        start = _date(b.get("from"), f"blackout {i + 1}")
        end = _date(b.get("to", b.get("from")), f"blackout {i + 1}")
        if end < start:
            raise PlannerError(f"blackout {i + 1}: 'to' is before 'from'")
        blackouts.append(Blackout(start, end, str(b.get("reason", "blackout"))))

    products: list[Product] = []
    seen: set[str] = set()
    for raw in raw_products:
        name = raw.get("name")
        if not name:
            raise PlannerError("product without name")
        if name in seen:
            raise PlannerError(f"duplicate product {name}")
        seen.add(name)
        for key in ("version", "target_version", "owner", "departments", "window"):
            if key not in raw:
                raise PlannerError(f"{name}: missing '{key}'")
        crit = raw.get("criticality", "medium")
        if crit not in CRITICALITY:
            raise PlannerError(f"{name}: criticality must be one of {', '.join(CRITICALITY)}")
        window = raw["window"]
        weekday = str(window.get("weekday", "")).lower()
        if weekday not in WEEKDAYS:
            raise PlannerError(f"{name}: invalid weekday {window.get('weekday')!r}")
        try:
            start_time = dt.time.fromisoformat(str(window.get("start", "")))
        except ValueError as exc:
            raise PlannerError(f"{name}: invalid window start {window.get('start')!r}") from exc
        duration = int(window.get("duration_minutes", 60))
        if not 0 < duration <= 24 * 60:
            raise PlannerError(f"{name}: duration_minutes must be 1..1440")
        for dep in raw["departments"]:
            if dep not in departments:
                raise PlannerError(f"{name}: unknown department {dep!r}")
        change_type(str(raw["version"]), str(raw["target_version"]))  # validates order
        products.append(Product(
            name=name,
            package=str(raw.get("package", "")),
            vendor=str(raw.get("vendor", "")),
            version=str(raw["version"]),
            target_version=str(raw["target_version"]),
            owner_name=str(raw["owner"].get("name", "")),
            owner_email=str(raw["owner"].get("email", "")),
            departments=list(raw["departments"]),
            criticality=crit,
            weekday=weekday,
            start_time=start_time,
            duration_minutes=duration,
            depends_on=list(raw.get("depends_on") or []),
        ))
    for p in products:
        for dep in p.depends_on:
            if dep not in seen:
                raise PlannerError(f"{p.name}: unknown dependency {dep!r}")
            if dep == p.name:
                raise PlannerError(f"{p.name}: depends on itself")

    return Inventory(
        timezone=str(planning.get("timezone", "UTC")),
        lead_time_days=int(planning.get("lead_time_days", 7)),
        max_changes_per_day=int(planning.get("max_changes_per_day", 1)),
        blackouts=blackouts,
        departments=departments,
        products=products,
    )


def load_inventory(path: Path) -> Inventory:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlannerError(f"inventory not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PlannerError(f"inventory is not valid YAML: {exc}") from exc
    return parse_inventory(data)


# --------------------------------------------------------------------------
# scheduling
# --------------------------------------------------------------------------

def dependency_order(products: list[Product]) -> list[Product]:
    """Topological order; ties broken by criticality (low first), then name."""
    by_name = {p.name: p for p in products}
    pending = {p.name: set(p.depends_on) for p in products}
    ready = [(CRITICALITY[p.criticality], p.name) for p in products if not p.depends_on]
    heapq.heapify(ready)
    ordered: list[Product] = []
    while ready:
        _, name = heapq.heappop(ready)
        ordered.append(by_name[name])
        for other, deps in pending.items():
            if name in deps:
                deps.remove(name)
                if not deps:
                    heapq.heappush(ready, (CRITICALITY[by_name[other].criticality], other))
    if len(ordered) != len(products):
        stuck = sorted(n for n, deps in pending.items() if deps)
        raise PlannerError(f"dependency cycle between: {', '.join(stuck)}")
    return ordered


def in_month_end_freeze(day: dt.date, freeze_days: int) -> bool:
    if freeze_days <= 0:
        return False
    last = calendar.monthrange(day.year, day.month)[1]
    return day.day > last - freeze_days


def blocked_reason(day: dt.date, product: Product, inv: Inventory) -> str | None:
    for b in inv.blackouts:
        if b.covers(day):
            return b.reason
    for dep in product.departments:
        freeze = int(inv.departments.get(dep, {}).get("month_end_freeze_days", 0) or 0)
        if in_month_end_freeze(day, freeze):
            return f"{dep} month-end freeze"
    return None


def build_plan(inv: Inventory, start: dt.date) -> list[Window]:
    earliest_allowed = start + dt.timedelta(days=inv.lead_time_days)
    per_day: dict[dt.date, int] = {}
    scheduled: dict[str, dt.date] = {}
    windows: list[Window] = []
    for product in dependency_order(inv.products):
        day = earliest_allowed
        for dep in product.depends_on:
            day = max(day, scheduled[dep] + dt.timedelta(days=1))
        target_wd = WEEKDAYS.index(product.weekday)
        for _ in range(SEARCH_HORIZON_DAYS):
            if (day.weekday() == target_wd
                    and blocked_reason(day, product, inv) is None
                    and per_day.get(day, 0) < inv.max_changes_per_day):
                break
            day += dt.timedelta(days=1)
        else:
            raise PlannerError(f"{product.name}: no free window within {SEARCH_HORIZON_DAYS} days")
        per_day[day] = per_day.get(day, 0) + 1
        scheduled[product.name] = day
        windows.append(Window(product, day, day - dt.timedelta(days=inv.lead_time_days)))
    windows.sort(key=lambda w: (w.start, w.product.name))
    return windows


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def _fmt_day(day: dt.date) -> str:
    return f"{day.strftime('%a')} {day.isoformat()}"


def _fmt_window(w: Window) -> str:
    end = w.end
    end_txt = end.strftime("%H:%M") if end.date() == w.day else end.strftime("%H:%M (+1 day)")
    return f"{w.start.strftime('%H:%M')}–{end_txt}"


def department_message(dept: str, windows: list[Window], inv: Inventory) -> str:
    lines = []
    if len(windows) == 1:
        w = windows[0]
        subject = (f"Planned maintenance: {w.product.name} on {_fmt_day(w.day)}, "
                   f"{_fmt_window(w)} ({inv.timezone})")
    else:
        subject = (f"Planned maintenance: {len(windows)} windows between "
                   f"{windows[0].day.isoformat()} and {windows[-1].day.isoformat()}")
    lines.append(f"Subject: {subject}")
    lines.append("")
    lines.append(f"Dear {dept} team,")
    lines.append("")
    lines.append("IT Operations will update software that your applications use. "
                 "During the windows below the listed services can be unavailable or slow:")
    lines.append("")
    for w in windows:
        lines.append(f"- {_fmt_day(w.day)}, {_fmt_window(w)} ({inv.timezone}): "
                     f"{w.product.name} {w.product.version} → {w.product.target_version} "
                     f"({w.change_type} update)")
    lines.append("")
    lines.append("What this means for you:")
    lines.append("- Please save your work and log off from the affected applications before the window starts.")
    lines.append("- Automated jobs (imports, interfaces) are paused and resume automatically afterwards.")
    if any(w.risk == "high" for w in windows):
        lines.append("- This change is rated high risk. A tested rollback is prepared; if it is needed, "
                     "the window can run until its planned end.")
    lines.append("")
    lines.append("We will confirm by e-mail when each window is closed. If a date collides with a "
                 "business-critical deadline, please answer to the owner listed below by the "
                 "notify-by date at the latest.")
    lines.append("")
    owners = sorted({(w.product.owner_name, w.product.owner_email) for w in windows})
    lines.append("Contact: " + "; ".join(f"{n} <{e}>" for n, e in owners))
    lines.append("")
    lines.append("Kind regards,")
    lines.append("IT Operations")
    return "\n".join(lines)


def render_markdown(windows: list[Window], inv: Inventory, start: dt.date) -> str:
    out = []
    out.append("# Maintenance plan")
    out.append("")
    out.append("Generated by `tools/maintenance_planner.py` from `inventory.yml` — do not edit by hand.")
    out.append("")
    out.append(f"- Plan start: {start.isoformat()} · timezone: {inv.timezone}")
    out.append(f"- Lead time: departments are informed at least {inv.lead_time_days} days before a window")
    out.append(f"- At most {inv.max_changes_per_day} change(s) per day; dependencies are updated first")
    out.append("")
    out.append("## Schedule")
    out.append("")
    out.append("| # | Date | Window | Product | Version | Change | Risk | Owner | Departments | Notify by |")
    out.append("|---|------|--------|---------|---------|--------|------|-------|-------------|-----------|")
    for i, w in enumerate(windows, 1):
        p = w.product
        out.append(f"| {i} | {_fmt_day(w.day)} | {_fmt_window(w)} | {p.name} | "
                   f"{p.version} → {p.target_version} | {w.change_type} | {w.risk} | "
                   f"{p.owner_name} | {', '.join(p.departments)} | {w.notify_by.isoformat()} |")
    out.append("")
    out.append("## Constraints applied")
    out.append("")
    for b in inv.blackouts:
        span = b.start.isoformat() if b.start == b.end else f"{b.start.isoformat()} – {b.end.isoformat()}"
        out.append(f"- Blackout {span}: {b.reason}")
    for dept, cfg in sorted(inv.departments.items()):
        freeze = int((cfg or {}).get("month_end_freeze_days", 0) or 0)
        if freeze:
            out.append(f"- {dept}: no changes in the last {freeze} days of a month")
    deps = [w for w in windows if w.product.depends_on]
    for w in deps:
        out.append(f"- {w.product.name} after {', '.join(w.product.depends_on)}")
    out.append("")
    out.append("## Per window checklist")
    out.append("")
    out.append("For every window: `scripts/preflight.sh --ports-mode listening` → "
               "`scripts/upgrade.sh --dry-run <product>` → backup (`scripts/backup_restore.sh backup` "
               "for database changes) → `scripts/upgrade.sh <product>` → `scripts/healthcheck.sh` → "
               "close the window and notify departments. Rollback: see "
               "`docs/runbooks/06-failed-upgrade.md`.")
    out.append("")
    out.append("## Communication per department")
    for dept in sorted(inv.departments):
        cfg = inv.departments[dept] or {}
        mine = [w for w in windows if dept in w.product.departments]
        out.append("")
        out.append(f"### {dept}")
        out.append("")
        out.append(f"Contact: {cfg.get('contact', 'n/a')} · windows: {len(mine)}"
                   + (f" · send by: {min(w.notify_by for w in mine).isoformat()}" if mine else ""))
        out.append("")
        if not mine:
            out.append("No maintenance affecting this department in this plan.")
            continue
        out.append("```text")
        out.append(department_message(dept, mine, inv))
        out.append("```")
    out.append("")
    return "\n".join(out)


def render_json(windows: list[Window]) -> str:
    return json.dumps([
        {
            "date": w.day.isoformat(),
            "start": w.start.strftime("%H:%M"),
            "end": w.end.strftime("%H:%M"),
            "product": w.product.name,
            "from": w.product.version,
            "to": w.product.target_version,
            "change": w.change_type,
            "risk": w.risk,
            "owner": w.product.owner_email,
            "departments": w.product.departments,
            "notify_by": w.notify_by.isoformat(),
        }
        for w in windows
    ], indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", type=Path, default=Path("inventory.yml"))
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="first day of the planning period (YYYY-MM-DD), default today")
    ap.add_argument("--output", type=Path, help="write to this file instead of stdout")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = ap.parse_args(argv)
    try:
        inv = load_inventory(args.inventory)
        windows = build_plan(inv, args.start)
    except PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = render_markdown(windows, inv, args.start) if args.format == "markdown" else render_json(windows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        print(f"wrote {args.output} ({len(windows)} windows)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
