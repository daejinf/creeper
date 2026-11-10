"""Code-owned IDs, preserving dependency relationships when assigning new task IDs."""
from uuid import uuid4

def assign_ids(plan, previous=None):
    ids={task.task_id:str(uuid4()) for task in plan.tasks if task.task_id}
    plan.plan_version_id=str(uuid4())
    if previous:plan.previous_plan_version_id=previous
    for task in plan.tasks:
        task.task_id=ids.get(task.task_id,str(uuid4()))
        task.dependencies=[ids.get(ref,ref) for ref in task.dependencies]
