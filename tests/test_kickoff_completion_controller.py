"""Focused unit contracts for the shared browser completion controller."""
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_node(source: str):
    result = subprocess.run(
        ['node', '-e', source], cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_status_labels_and_idempotent_action_ids():
    controller_path = json.dumps(str(ROOT / 'tools' / 'bench' / 'kickoff-completion.js'))
    run_node(f"""
global.window=globalThis;
global.document={{getElementById:()=>null}};
const stored=new Map();
global.localStorage={{
  getItem:key=>stored.has(key)?stored.get(key):null,
  setItem:(key,value)=>stored.set(key,String(value)),
  removeItem:key=>stored.delete(key),
}};
require({controller_path});
const expected={{
  TODO:'제출 전',SUBMITTED:'제출 저장됨',REVIEWING:'AI 검토 중',
  REVIEW_NEEDED:'추가 확인 필요',NEEDS_INPUT:'자료 보완 필요',
  APPEALED:'동료 투표 중',COMPLETED:'완료',REVIEW_FAILED:'AI 검토 실패',
}};
for(const [status,label] of Object.entries(expected)){{
  if(window.kickoffCompletion.statusLabel(status)!==label)throw Error(status+' label mismatch');
}}
const actionIds=[];let call=0;
global.fetch=async(path,options)=>{{
  call+=1;actionIds.push(options.body.get('action_id'));
  if(call===1||call===3)throw new TypeError('response lost');
  return new Response(JSON.stringify({{status:'SUBMITTED'}}),{{status:200,headers:{{'Content-Type':'application/json'}}}});
}};
async function submit(description){{
  return window.kickoffCompletion.submitEvidence({{
    groupId:'g1',memberId:'m1',planId:'p1',taskId:'t1',description,files:[],
  }});
}}
(async()=>{{
  try{{await submit('같은 입력');}}catch{{}}
  await submit('같은 입력');
  try{{await submit('변경 전 입력');}}catch{{}}
  await submit('변경 후 입력');
  if(!actionIds[0]||actionIds[0]!==actionIds[1])throw Error('lost-response retry changed action_id');
  if(!actionIds[2]||actionIds[2]===actionIds[3])throw Error('changed input reused action_id');
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
""")
