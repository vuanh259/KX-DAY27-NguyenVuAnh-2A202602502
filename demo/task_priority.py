"""Standalone task-priority example for the HITL review lab."""
from dataclasses import dataclass
from enum import IntEnum


class Priority(IntEnum):
    LOW = 1
    NORMAL = 2
    HIGH = 3


@dataclass(frozen=True)
class Task:
    title: str
    priority: Priority = Priority.NORMAL

    def __post_init__(self):
        if not self.title.strip():
            raise ValueError("Task title must not be blank")
        object.__setattr__(self, "priority", Priority(self.priority))


def sort_tasks(tasks: list[Task]) -> list[Task]:
    """Return a new list, highest priority first; ties keep input order."""
    return sorted(tasks, key=lambda task: task.priority, reverse=True)
