"""Single source of authored requests: examples/requests/*.json. No AI call."""
from copy import deepcopy
import json
from pathlib import Path

_REQUESTS = Path(__file__).with_name('requests')

def sample(module):
    return json.loads((_REQUESTS/(module+'.json')).read_text(encoding='utf-8'))

MEMBERS = sample('A')['members']
CONTEXT = sample('E.coach')['context']
GOAL = sample('D')
MESSAGES = sample('A')['conversation']

def plan(owner=None, minutes=20):
    value = deepcopy(sample('F')['group_plan'])
    value['owner_member_id'] = owner
    for task in value['tasks']:
        task['owner_member_id'] = owner
        task['estimated_minutes'] = minutes
    return value
