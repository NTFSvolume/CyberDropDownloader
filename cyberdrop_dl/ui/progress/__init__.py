from typing import  NamedTuple
class TaskInfo(NamedTuple):
    id: int
    description: str
    completed: int
    total: int
    progress: float