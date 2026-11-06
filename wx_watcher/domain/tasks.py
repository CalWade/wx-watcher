"""Explicit transitions for a reply task under human review."""

from copy import deepcopy

REVIEWABLE = frozenset({"draft", "needs_review", "delivery_review", "budget_review"})


def review_task(task: dict | None, decision: dict, *, timestamp: str, retry_id: str):
    if task is None or task["status"] not in REVIEWABLE:
        raise ValueError("任务状态不支持此操作，请刷新后检查")
    updated = deepcopy(task)
    action = decision.get("decision")
    if action == "dismiss":
        updated.update(status="dismissed", reviewed_at=timestamp)
        return [updated]
    if action == "mark-delivered":
        if task["status"] != "delivery_review" or decision.get("confirmed") is not True:
            raise ValueError("任务处理选项无效")
        updated.update(status="sent", reviewed_at=timestamp, manually_confirmed=True)
        return [updated]
    if action != "retry":
        raise ValueError("任务处理选项无效")
    if task["status"] == "delivery_review":
        if decision.get("confirmed_not_delivered") is not True:
            raise ValueError("请先在飞书核对确实未投递，才能重新安排发送")
        updated.update(status="draft", reviewed_at=timestamp, error=None)
        return [updated]
    retry = {
        **updated,
        "id": retry_id,
        "retry_of": task["id"],
        "status": "queued",
        "created_at": timestamp,
    }
    for field in (
        "agent",
        "proposal",
        "error",
        "delivery",
        "feishu_text",
        "drafted_at",
    ):
        retry.pop(field, None)
    updated.update(status="resolved", reviewed_at=timestamp, replacement_id=retry_id)
    return [retry, updated]
