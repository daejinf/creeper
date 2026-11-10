"""Opt-in GPT migration check. Persistent cap: 12 HTTP including retries/repairs.

Inputs are anonymous authored fixtures, not real user approvals. Success files
are reused. Failed stages require --retry-failed; no other model is substituted.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from wemeet_ai.cli import execute
from wemeet_ai.m0 import AIError, Runtime, TimelyClient
from wemeet_ai.shared.privacy import sanitize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from _data import sample


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Budget:
    def __init__(self, folder): self.path = folder / 'budget.json'
    def reserve(self):
        data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'http': 0, 'limit': 12}
        if data['http'] >= 12: raise AIError('EVALUATION_BUDGET', 'Migration HTTP limit (12) reached')
        data['http'] += 1
        write(self.path, data)  # Reserve before network, even when transport fails.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'runtime' / 'timely-013'
    tasks = ['A', 'B', 'C', 'D', 'E.coach', 'E.plan']
    inputs = {task: sample(task) for task in tasks}
    manifest = {'version': '0.13.0', 'model': os.getenv('TIMELY_MODEL', 'openai/gpt-5.6-luna'),
                'inputs': inputs, 'max_http': 12, 'reference': 'AUTHORED_ANONYMOUS_TEST_INPUTS'}
    path = folder / 'manifest.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) != manifest:
        raise SystemExit('Inputs/model changed. Preserve the existing migration results; do not mix runs.')
    write(path, manifest)
    if not args.live or not os.getenv('TIMELY_API_KEY'):
        write(folder / 'status.json', {'status': 'NOT_RUN_KEY_MISSING' if args.live else 'MANIFEST_ONLY',
                                     'live_http_this_execution': 0})
        print('No paid call. Register TIMELY_API_KEY and explicitly run with --live.')
        return
    budget = Budget(folder)
    for task in tasks:
        path = folder / (task + '.json')
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if previous['response']['status'] == 'OK' or not args.retry_failed: continue
            archive = folder / 'attempts' / (task + '-' + previous['response']['meta']['run_id'] + '.json')
            write(archive, previous)
        result = execute(task, inputs[task], runtime=Runtime(TimelyClient(budget=budget)), fresh=True)
        write(path, {'module': task, 'input': inputs[task], 'response': result.model_dump(mode='json'),
                     'semantic_review': 'PENDING', 'coaching_10s_target_met': result.status == 'OK' and result.meta.latency_ms <= 10000 if task == 'A' else None})
        print(task, result.status, result.meta.transport_attempts, 'HTTP', result.meta.latency_ms, 'ms')
        if result.error and result.error.code in {'AUTH_FAILED', 'INSUFFICIENT_CREDITS', 'MODEL_NOT_AVAILABLE', 'EVALUATION_BUDGET'}: break
    write(folder / 'status.json', {'status': 'RESULTS_SAVED_SEMANTIC_REVIEW_REQUIRED',
                                  'budget': json.loads(budget.path.read_text(encoding='utf-8')) if budget.path.exists() else {'http': 0, 'limit': 12}})


if __name__ == '__main__': main()
