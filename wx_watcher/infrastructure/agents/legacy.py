"""Read task records created before the provider-neutral agent contract."""

from .dsh import DshGateway


def upgrade_task(task):
    task = dict(task)
    task.setdefault("agent_provider", "dsh")
    if "agent" not in task and isinstance(task.get("dsh"), dict):
        raw = task["dsh"]
        task["agent"] = DshGateway(None)._job(task["id"], raw).as_dict()
    task.pop("dsh", None)
    return task
