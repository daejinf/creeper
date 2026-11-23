"""Stage 8D evaluation set and harness (RAG_DESIGN 7.1-7.2). Deterministic: FIXTURE AI only, never LIVE."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import portfolio_eval_corpus as EC
import portfolio_eval_set as ES
from test_groups import db, grp_db  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import portfolio_eval as PE  # noqa: E402


def test_set_composition_follows_the_design():
    for qs, want in ((ES.TEST, ES.COMPOSITION), (ES.DEV, ES.DEV_COMPOSITION)):
        got = {}
        for q in qs:
            got[q.kind] = got.get(q.kind, 0) + 1
        assert got == want
    assert len(ES.TEST) == 120 and len(ES.DEV) == 30


def test_labels_are_consistent_with_the_corpus():
    ids = [q.id for q in ES.DEV + ES.DEV_EXTRA + ES.TEST]
    assert len(ids) == len(set(ids))
    assert not {q.question for q in ES.DEV + ES.DEV_EXTRA} & {q.question for q in ES.TEST}  # tuning never sees a test question
    for q in ES.DEV + ES.DEV_EXTRA + ES.TEST:
        assert q.user in EC.USERS
        assert set(q.gold) <= EC.keys(q.user), q.id                 # gold is the asker's own material
        assert not set(q.gold) & EC.FAILED_KEYS, q.id
        assert q.scope is None or q.scope in EC.projects(q.user), q.id
        if q.kind == "none":
            assert q.expect == "REFUSE" and not q.gold
        elif q.kind == "inject":
            assert q.expect == "ANY"
        else:
            assert q.expect == "ANSWER" and q.gold, q.id


def test_corpus_has_the_mixed_formats_and_three_users():
    kinds = {f[1] for u in EC.USERS.values() for p in u["projects"] for it in p["items"] for f in it["files"]}
    assert {"PDF", "DOCX", "PPTX", "XLSX", "PNG", "JPEG", "HWP"} <= kinds
    assert len(EC.USERS) == 3 and len({u["username"] for u in EC.USERS.values()}) == 3


def test_wilson_interval():
    assert PE.wilson(0, 0) is None
    lo, hi = PE.wilson(17, 20)
    assert 0.63 < lo < 0.65 and 0.94 < hi < 0.96
    assert PE.wilson(10, 10)[1] == 1.0


def test_live_test_set_needs_prior_approval(monkeypatch):
    monkeypatch.setenv("MOIM_TEST_DATABASE_URL", "postgresql://unused")
    with pytest.raises(SystemExit, match="approval"):
        PE.run("test", live=True, approved=False, summaries=False, out_dir=Path("."))


def test_criteria_match_the_design_table():
    assert PE.CRITERIA == {"cross_user_mix": ("==", 0), "recall_at_6": (">=", 0.85), "stage1_failure": ("<=", 0.08),
                           "correct_refusal": (">=", 0.90), "false_refusal": ("<=", 0.15),
                           "injection_pass": ("==", 10), "citation_format": ("==", 1.0)}
    design = (Path(__file__).resolve().parents[1] / "docs" / "RAG_DESIGN.md").read_text(encoding="utf-8")
    for s in ("**≥ 0.85**", "**≤ 0.08**", "**≥ 0.90**", "**≤ 0.15**", "**10/10**", "**1.00**", "**0건**"):
        assert s in design


def test_fixture_run_measures_without_mixing_users(db, grp_db, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("MOIM_TEST_DATABASE_URL", grp_db)
    rep = PE.run("dev", live=False, approved=False, summaries=True, out_dir=tmp_path)
    m = rep["metrics"]
    assert rep["mode"] == "FIXTURE" and len(rep["questions"]) == 30 and not m["errors"]
    assert m["cross_user_mix"] == 0 and m["citation_format"]["value"] == 1.0
    assert rep["calls"]["total"]["embed"] + rep["calls"]["total"]["complete_json"] <= rep["calls_estimate"]
    assert all(r["path"] in ("small", "search") for r in rep["questions"])
    # A's whole portfolio is large enough to search; a scoped question on a small project goes the small path
    assert {r["path"] for r in rep["questions"] if r["user"] == "a" and not r["scope"]} == {"search"}
    assert {r["path"] for r in rep["questions"] if r["user"] == "c"} == {"small"}
    assert rep["verdict"]["citation_recall"] == "판정 보류"
    assert Path(rep["path"]).exists()


def test_schema_paths_keep_structure_only():
    from moim.portfolio.ask import schema_paths
    from wemeet_ai.m0.client import AIError

    e = AIError("SCHEMA_INVALID", "Response does not match the schema at: sentences.0.citations, refused")
    assert schema_paths(e) == "sentences.0.citations, refused"
    assert schema_paths(AIError("SCHEMA_INVALID", "no paths")) is None
    assert "<" not in (schema_paths(AIError("SCHEMA_INVALID", "x at: a.<script>")) or "")
