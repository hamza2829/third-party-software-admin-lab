PLAN_START ?= 2026-12-01

.PHONY: lint test plan check lab

lint:
	shellcheck -x scripts/*.sh scripts/lib/*.sh
	yamllint .
	ansible-lint ansible/

test:
	python3 -m pytest -v

plan:
	python3 tools/maintenance_planner.py --start $(PLAN_START) --output docs/maintenance-plan.md

check: lint test

# On an AlmaLinux/Rocky 9 host or VM, as root:
lab:
	scripts/preflight.sh
	scripts/bootstrap.sh
	PATH=/opt/lab/venv/bin:$$PATH scripts/run_playbook.sh
	PATH=/opt/lab/venv/bin:$$PATH scripts/run_playbook.sh --assert-idempotent
	scripts/healthcheck.sh
