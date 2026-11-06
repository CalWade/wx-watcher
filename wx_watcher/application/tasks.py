"""Application commands for Tasks; concrete adapters are supplied by composition."""

from __future__ import annotations

import uuid

from ..domain.replies import hash_id
from ..domain.tasks import review_task as review_task_transition
from .ports import TasksContext


class TasksUseCases:
    operations = ("task-review", "reset-cursor")

    def __init__(self, context: TasksContext):
        self.context = context

    def handle(self, operation, data):
        if operation == "task-review":
            return self.review(data)
        if operation == "reset-cursor":
            if self.context.services.running() or data.get("confirmed") is not True:
                raise ValueError("请先暂停监听，并确认接受已过期事件无法补回的缺口")
            return self.context.tasks.reset_cursor()
        raise ValueError("未知操作")

    def review(self, data):
        if self.context.services.running():
            raise ValueError("请先暂停监听，再处理任务；处理后可重新开始监控")
        with self.context.tasks.open() as repository:
            changes = review_task_transition(
                repository.task(str(data.get("id", ""))),
                data,
                timestamp=self.context.clock.timestamp(),
                retry_id=hash_id(uuid.uuid4().hex),
            )
            repository.save_many(changes)
        return {"saved": True, "message": "任务已处理；重新开始监控后执行待办"}
