"""Process-start source revisions. No database/web imports or model requests.

Editing disk does not change an already imported Python function. Keep the
loaded snapshot separate, and require reload before calling changed code.
"""
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TASKS=('A','B','C','D','E.coach','E.draft','E.plan','F')


def read_sources():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(ROOT.rglob('*.py'))}


def revision_for(task,sources):
    name=task.split('.')[0]
    folder={'A':'a_coaching','B':'b_analysis','C':'c_goal_draft',
        'D':'d_group_plan','E':'e_member_plan','F':'f_validation'}.get(name)
    prefixes=['m0/','shared/']
    if folder:prefixes.append('m1/'+folder+'/')
    if name=='D' or task=='E.plan':prefixes.append('m1/f_validation/')
    selected=[(path,digest) for path,digest in sorted(sources.items())
        if path=='__init__.py' or any(path.startswith(prefix) for prefix in prefixes)]
    return hashlib.sha256(repr(selected).encode()).hexdigest()[:16]


LOADED_SOURCES=read_sources()


def loaded_revision(task):return revision_for(task,LOADED_SOURCES)


def reload_required(task):return revision_for(task,read_sources())!=loaded_revision(task)


def result_code_status(result,task):
    if not result:return 'NOT_RUN'
    if reload_required(task):return 'RELOAD_PENDING'
    revision=result.get('meta',{}).get('source_revision')
    if not revision:return 'UNTRACKED'
    return 'CURRENT' if revision==loaded_revision(task) else 'OUTDATED'


def reusable_result(result,task):return bool(result and result.get('status')=='OK' and result_code_status(result,task)=='CURRENT')


def code_status():
    disk=read_sources()
    return {'modules':{task:{'loaded':loaded_revision(task),'disk':revision_for(task,disk)} for task in TASKS},
        'reload_required':disk!=LOADED_SOURCES}
