import subprocess, sys
from pathlib import Path


def test_core_import_is_independent_of_web_database_and_old_project():
    script = """
import sys
from wemeet_coach import group_coach,personal_coach,GroupRequest,validate_plan,PersonalRequest,Result
assert not any(x.split('.')[0] in {'fastapi','sqlalchemy','psycopg','wemeet_ai'} for x in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_documented_examples_parse_and_check_without_paid_call():
    subprocess.run([sys.executable, "examples/core.py", "--check"], check=True)
    subprocess.run([sys.executable, "examples/stored_flow.py"], check=True)


def test_single_list_value_is_normalized_without_changing_meaning():
    from wemeet_coach.personal import normalize

    assert normalize("tools", "스마트폰 메모") == ["스마트폰 메모"]
    assert normalize("constraints", []) == []


def test_personal_dialogue_schema_does_not_include_plan_generation():
    from wemeet_coach.personal import PersonalTalkReply
    import json

    schema = json.dumps(PersonalTalkReply.model_json_schema())
    assert "PersonalPlan" not in schema and "Task" not in schema
