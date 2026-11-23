# Questions to a software vendor – templates

Copy the block you need, fill in the `<...>` parts, delete what does not apply.
Rule of thumb: one question per message, all facts the vendor will ask for in the first message, no passwords or
customer data in log excerpts.

## Collect first

```bash
cat /etc/os-release | grep PRETTY_NAME; uname -r
rpm -q <package> --qf '%{NAME} %{VERSION}-%{RELEASE} %{ARCH} (installed %{INSTALLTIME:date})\n'
rpm -q erlang                                     # RabbitMQ: Erlang version matters
dnf repolist; dnf history list <package> | head
rabbitmq-diagnostics -q status | head -n 30       # RabbitMQ
psql -U postgres -Atc 'select version()'          # PostgreSQL
```

---

## 1. Version and compatibility question

```text
Subject: [<Product>] Supported upgrade path <current version> -> <target version> on <OS> <OS version>

Hello <vendor> support,

we run <product> <current version> (package <package>-<version-release>, installed from <repository>)
on <AlmaLinux 9.x / Rocky Linux 9.x>, kernel <uname -r>, <x86_64>.
Customer number / contract: <number>

We plan to update to <target version> in a maintenance window on <date>.

1. Is <target version> supported on <OS + version>? Is the update from <current version> direct, or do we need an
   intermediate version?
2. Which versions of <dependency, e.g. Erlang/OTP, PostgreSQL client, Java> are supported for <target version>?
3. Are there configuration settings that were removed or renamed between these versions?
4. Is a rollback to <current version> supported after the update (data/schema changes, feature flags)?
5. Until when is <current version> supported (end of maintenance / end of life)?

Thank you,
<name>, <team>, <phone>
```

## 2. Support case

```text
Subject: [<Product> <version>] <one-line description of the problem> – impact: <none|degraded|outage>

Severity requested: <1 outage / 2 major function down / 3 minor / 4 question>
Contract / customer number: <number>
Affected system: <hostname role, e.g. "order processing message broker, production">

Environment
- Product: <product> <version-release> (package <package>, repository <repo>)
- OS: <AlmaLinux 9.x>, kernel <...>, <VM / container>, <CPU> vCPU, <RAM> GB RAM
- Dependencies: <Erlang 27.x / PostgreSQL 16.x / ...>
- Recent changes: <upgrade on <date> from <version>, config change, none>

Problem
- Since: <date time timezone>
- Expected: <...>
- Actual: <... exact error message ...>
- Frequency: <always / intermittent, n times per hour>
- Business impact: <which departments, what does not work>

What we already checked
- <healthcheck output / runbook steps taken, results>

Attached
- Log excerpt (see below), configuration files without secrets, output of <diagnostic command>

Contact for this case: <name>, <phone>, available <hours>
```

## 3. Log excerpt

Send the lines around the first error, not the whole file, and say where they come from.

```text
Source: /var/log/rabbitmq/rabbit@<node>.log  (time zone UTC)
Window: 2026-12-19 08:12:00 – 08:14:30, first error at 08:12:41

2026-12-19 08:12:41.123 [error] <0.523.0> ...
2026-12-19 08:12:41.124 [error] <0.523.0> ...
...
(passwords and customer names replaced by <redacted>)
```

Useful commands:

```bash
grep -n -m1 -E 'error|FATAL|PANIC' <logfile>                 # find the first error
sed -n '<line-20>,<line+40>p' <logfile> > excerpt.txt         # context around it
sed -i -E 's/(password=)[^ ]+/\1<redacted>/g' excerpt.txt     # remove secrets
journalctl -u <unit> --since '<time>' --until '<time>' --no-pager
```

## 4. Steps to reproduce

```text
Steps to reproduce (on <test system / production>):

Preconditions: <product> <version>, config as attached, <n> messages in queue <name> / table with <n> rows

1. <exact command or UI action>
2. <...>
3. <...>

Expected result: <...>
Actual result:   <... + exact error text ...>
Reproducible:    <always / 3 of 10 tries>
Not reproducible on: <other version / other host>, which suggests <...>
Workaround in place: <none / description>
```

## 5. Follow-up / escalation

```text
Subject: RE: Case <number> – status request / escalation

Hello <name>,

case <number> has been open since <date> with severity <n>; last update from your side on <date>.
Current impact: <...>. Our next maintenance window is <date>; we need <a fix / a statement whether rollback is safe>
by <date> to plan it.

Could you please give us an update and the next step with a time estimate? If needed, we can provide <remote
session / additional logs> on <dates>.

Regards,
<name>
```
