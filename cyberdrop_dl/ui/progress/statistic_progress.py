from typing import Dict, Union

from rich.console import Group
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TaskID
from cyberdrop_dl.utils.utilities import log
from cyberdrop_dl.ui.progress import TaskInfo

async def get_task_info_sorted(progress: Progress):
    tasks = [
        TaskInfo(
            id=task.id,
            description=task.description,
            completed=task.completed,
            total=task.total,
            progress=(task.completed / task.total if task.total else 0)
        )
        for task in progress.tasks
    ]

    tasks_sorted = sorted(tasks, key=lambda x: x.completed, reverse=True)

    return tasks_sorted

class DownloadStatsProgress:
    """Class that keeps track of download failures and reasons"""

    def __init__(self):
        self.progress = Progress("[progress.description]{task.description}",
                                 BarColumn(bar_width=None),
                                 "[progress.percentage]{task.percentage:>6.2f}%",
                                 "━",
                                 "{task.completed}")
        self.progress_group = Group(self.progress)

        self.failure_types: Dict[str, TaskID] = {}
        self.failed_files = 0
        self.panel = Panel(self.progress_group, title="Download Failures", border_style="green", padding=(1, 1), subtitle = f"Total Download Failures: {self.failed_files}")

    async def get_progress(self) -> Panel:
        """Returns the progress bar"""
        return self.panel

    async def update_total(self, total: int) -> None:
        """Updates the total number download failures"""
        self.panel.subtitle = f"Total Download Failures: {self.failed_files}"
        for key in self.failure_types:
            self.progress.update(self.failure_types[key], total=total)
        
        # Sort tasks on UI
        tasks_sorted = await get_task_info_sorted(self.progress)

        for task_id in [task.id for task in tasks_sorted]:
            self.progress.remove_task(task_id)

        for task in tasks_sorted:
            self.failure_types[task.description] = self.progress.add_task(task.description, total=task.total, completed=task.completed)


    async def add_failure(self, failure_type: Union[str, int]) -> None:
        """Adds a failed file to the progress bar"""
        self.failed_files += 1
        if isinstance(failure_type, int):
            failure_type = str(failure_type) + " HTTP Status"

        if failure_type in self.failure_types:
            self.progress.advance(self.failure_types[failure_type], 1)
        else:
            self.failure_types[failure_type] = self.progress.add_task(failure_type, total=self.failed_files,
                                                                      completed=1)
        await self.update_total(self.failed_files)

    async def return_totals(self) -> Dict:
        """Returns the total number of failed files"""
        failures = {}
        for key, value in self.failure_types.items():
            failures[key] = self.progress.tasks[value].completed
        return dict(sorted(failures.items()))


class ScrapeStatsProgress:
    """Class that keeps track of scraping failures and reasons"""

    def __init__(self):
        self.progress = Progress("[progress.description]{task.description}",
                                 BarColumn(bar_width=None),
                                 "[progress.percentage]{task.percentage:>6.2f}%",
                                 "━",
                                 "{task.completed}")
        self.progress_group = Group(self.progress)

        self.failure_types: Dict[str, TaskID] = {}
        self.failed_files = 0
        self.panel = Panel(self.progress_group, title="Scrape Failures", border_style="green", padding=(1, 1), subtitle = f"Total Scrape Failures: {self.failed_files}")

    async def get_progress(self) -> Panel:
        """Returns the progress bar"""
        return self.panel

    async def update_total(self, total: int) -> None:
        """Updates the total number of scrape failures"""
        self.panel.subtitle = f"Total Scrape Failures: {self.failed_files}"
        for key in self.failure_types:
            self.progress.update(self.failure_types[key], total=total)

        # Sort tasks on UI
        tasks_sorted = await get_task_info_sorted(self.progress)

        for task_id in [task.id for task in tasks_sorted]:
            self.progress.remove_task(task_id)

        for task in tasks_sorted:
            self.failure_types[task.description] = self.progress.add_task(task.description, total=task.total, completed=task.completed)

    async def add_failure(self, failure_type: Union[str, int]) -> None:
        """Adds a failed site to the progress bar"""
        self.failed_files += 1
        if isinstance(failure_type, int):
            failure_type = str(failure_type) + " HTTP Status"

        if failure_type in self.failure_types:
            self.progress.advance(self.failure_types[failure_type], 1)
        else:
            self.failure_types[failure_type] = self.progress.add_task(failure_type, total=self.failed_files,
                                                                      completed=1)
        await self.update_total(self.failed_files)

    async def return_totals(self) -> Dict:
        """Returns the total number of failed sites and reasons"""
        failures = {}
        for key, value in self.failure_types.items():
            failures[key] = self.progress.tasks[value].completed
        return dict(sorted(failures.items()))
