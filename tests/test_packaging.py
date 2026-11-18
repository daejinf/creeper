"""Only KEEP/ADAPT code is packaged and runnable; DROP/REPLACE modules and the old CLI are gone (docs/ARCHITECTURE.md)."""
import importlib
import importlib.util
import os
import pkgutil
import subprocess
import sys
import zipfile
from importlib.metadata import distributions, entry_points
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

EXPECTED_MODULES = {
    "wemeet_ai.m0", "wemeet_ai.m0.client", "wemeet_ai.m0.records", "wemeet_ai.m0.runtime", "wemeet_ai.m0.tracing",
    "wemeet_ai.m1", "wemeet_ai.m1.e_member_plan", "wemeet_ai.m1.f_validation",
    "wemeet_ai.m2", "wemeet_ai.m2.completion_review", "wemeet_ai.m2.contracts", "wemeet_ai.m2.evidence",
    "wemeet_ai.m2.prompts",
    "wemeet_ai.shared", "wemeet_ai.shared.calendar", "wemeet_ai.shared.contracts", "wemeet_ai.shared.normalization",
    "wemeet_ai.shared.plans", "wemeet_ai.shared.privacy",
}
REMOVED = [
    "wemeet_ai.m1.a_coaching", "wemeet_ai.m1.b_analysis", "wemeet_ai.m1.c_goal_draft", "wemeet_ai.m1.d_group_plan",
    "wemeet_ai.persistence", "wemeet_ai.persistence.execution", "wemeet_ai.persistence.schema", "wemeet_ai.continuity",
    "wemeet_ai.cli", "wemeet_ai.m0.versions", "wemeet_ai.m0.cache",
]


def test_module_set_is_exactly_the_kept_modules():
    import wemeet_ai

    found = {m.name for m in pkgutil.walk_packages(wemeet_ai.__path__, "wemeet_ai.")}
    assert found == EXPECTED_MODULES


@pytest.mark.parametrize("name", sorted(EXPECTED_MODULES))
def test_every_kept_module_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("name", REMOVED)
def test_removed_modules_cannot_be_imported(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


def test_no_removed_module_is_referenced_by_kept_code():
    import re

    imports = re.compile(r"^\s*(?:from|import)\s+[.\w]*\b(a_coaching|b_analysis|c_goal_draft|d_group_plan|persistence|"
                         r"continuity|versions|cache|cli)\b", re.M)
    hits = []
    for path in list((SRC / "wemeet_ai").rglob("*.py")) + list((SRC / "moim").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.relative_to(ROOT)}: import of {m.group(1)}" for m in imports.finditer(text)]
        if "SOURCE_RELOAD_REQUIRED" in text:
            hits.append(f"{path.relative_to(ROOT)}: SOURCE_RELOAD_REQUIRED")
    assert hits == []


def test_old_cli_does_not_run():
    run = subprocess.run([sys.executable, "-m", "wemeet_ai.cli", "--help"], capture_output=True, text=True,
                         cwd=ROOT, env={**os.environ, "PYTHONPATH": str(SRC)})
    assert run.returncode != 0 and "No module named" in run.stderr


def test_no_stale_distribution_or_console_script():
    names = {(d.metadata["Name"] or "").lower().replace("_", "-") for d in distributions()}
    assert "wemeet-ai-core" not in names and "wemeet-ai" not in names
    assert not [e for e in entry_points(group="console_scripts") if "wemeet" in e.name]
    assert not list(SRC.glob("wemeet*egg-info"))
    assert not list(Path(sys.executable).parent.glob("wemeet-ai*"))


def test_project_defines_no_console_scripts_and_no_old_package_name():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" not in text and "wemeet-ai" not in text


def test_built_wheel_contains_only_kept_packages_and_the_static_files(tmp_path):
    run = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(tmp_path), str(ROOT)],
                         capture_output=True, text=True)
    if run.returncode != 0 and any(s in run.stderr for s in ("Connection", "Could not find a version", "No matching distribution")):
        pytest.skip("cannot fetch the build backend (no network)")
    assert run.returncode == 0, run.stderr[-800:]
    with zipfile.ZipFile(next(tmp_path.glob("moim-*.whl"))) as z:
        names = z.namelist()
        entry = [z.read(n) for n in names if n.endswith("entry_points.txt")]
    modules = {n[:-3].replace("/", ".").removesuffix(".__init__") for n in names
               if n.endswith(".py") and n.startswith("wemeet_ai/")}
    assert modules == EXPECTED_MODULES | {"wemeet_ai"}
    assert "moim/static/job-poller.js" in names
    assert not [n for n in names if any(w in n for w in ("a_coaching", "persistence", "continuity", "cli.py", "cache.py", "versions.py"))]
    assert not [e for e in entry if b"wemeet" in e]
