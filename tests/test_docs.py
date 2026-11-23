import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNBOOKS = REPO / "docs" / "runbooks"
EXPECTED = ["01-service-does-not-start.md", "02-port-in-use.md", "03-disk-full.md",
            "04-wrong-credentials.md", "05-queue-backlog.md", "06-failed-upgrade.md"]


@pytest.mark.parametrize("name", EXPECTED)
def test_runbook_has_required_sections(name):
    text = (RUNBOOKS / name).read_text()
    for heading in ("## Symptom", "## Checks", "## Fix", "## Verify"):
        assert heading in text, f"{name} lacks {heading}"


def test_every_runbook_referenced_by_scripts_exists():
    refs = set()
    for script in (REPO / "scripts").glob("*.sh"):
        refs |= set(re.findall(r"\b0\d-[a-z-]+\.md", script.read_text()))
    assert refs, "scripts should point to runbooks"
    assert refs <= set(EXPECTED)


def test_vendor_question_templates():
    text = (REPO / "docs" / "vendor-questions.md").read_text()
    for heading in ("Version and compatibility", "Support case", "Log excerpt", "Steps to reproduce"):
        assert heading in text
