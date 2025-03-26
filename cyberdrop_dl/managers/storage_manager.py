from __future__ import annotations

import asyncio
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

import psutil

from cyberdrop_dl.clients.errors import InsufficientFreeSpaceError
from cyberdrop_dl.utils.logger import log_debug

if TYPE_CHECKING:
    from cyberdrop_dl.managers.manager import Manager
    from cyberdrop_dl.utils.data_enums_classes.url_objects import MediaItem


class StorageManager:
    """Runs an infinite loop to keep an updated value of the available space on all storage devices."""

    def __init__(self, manager: Manager):
        self.manager = manager
        self.total_data_written: int = 0
        self._used_mounts: set[Path] = set()
        self._free_space: dict[Path, int] = {}
        self._pause_if_no_free_space = True
        self._mount_addition_locks: dict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._updated = asyncio.Event()
        self._period: int = 2  # how often the check_free_space_loop will run (in seconds)
        self._log_period: int = 10  # log storage details every <x> loops, AKA log every 20 (2x10) seconds,
        self._loop = asyncio.create_task(self._check_free_space_loop())

    def get_used_mounts_stats(self) -> dict:
        data = {}
        for mount in self._used_mounts:
            data[mount] = get_available_partitions()[mount]._asdict()
            data[mount]["free_space"] = self._free_space[mount]
        return data

    async def check_free_space(self, media_item: MediaItem, no_pause: bool = False) -> None:
        """Checks if there is enough free space on download this item"""

        if not await self._has_sufficient_space(media_item.download_folder):
            if self._pause_if_no_free_space and not no_pause:
                self.manager.states.RUNNING.clear()
                await self.manager.states.RUNNING.wait()
                return await self.check_free_space(media_item, no_pause=True)
            raise InsufficientFreeSpaceError(origin=media_item)

    async def reset(self):
        await self._updated.wait()  # Make sure a query is not running right now
        self.total_data_written = 0
        self._used_mounts = set()
        self._free_space = {}

    async def close(self) -> None:
        await self.reset()
        self._loop.cancel()
        try:
            await self._loop
        except asyncio.CancelledError:
            pass

    async def _has_sufficient_space(self, folder: Path) -> bool:
        """Checks if there is enough free space to download to this folder"""

        mount = get_mount_point(folder)
        if not mount:
            return False

        async with self._mount_addition_locks[mount]:
            if mount not in self._free_space:
                # Manually query this mount now. Next time it will be part of the loop
                result = await asyncio.to_thread(psutil.disk_usage, str(mount))
                self._free_space[mount] = result.free
                self._used_mounts.add(mount)

        return self._free_space[mount] > self.manager.config_manager.global_settings_data.general.required_free_space

    async def _check_free_space_loop(self) -> None:
        """Infinite loop to get free space of all used mounts and update internal dict"""

        last_check = -1
        while True:
            # We could also update the values every 512MB of data written (MIN_REQUIRED_FREE_SPACE)
            # if self.data_writen // MIN_REQUIRED_FREE_SPACE <= last_check:
            #    continue
            # But every second is more accurate
            await self.manager.states.RUNNING.wait()
            self._updated.clear()
            last_check += 1
            if self._used_mounts:
                used_mounts = sorted(self._used_mounts)
                tasks = [asyncio.to_thread(psutil.disk_usage, str(mount)) for mount in used_mounts]
                results = await asyncio.gather(*tasks)
                for mount, result in zip(used_mounts, results, strict=True):
                    self._free_space[mount] = result.free
                if last_check % self._log_period == 0:
                    log_debug({"Storage status": self.get_used_mounts_stats()})
            self._updated.set()
            await asyncio.sleep(self._period)


def get_mount_point(folder: Path) -> Path | None:
    mounts = get_available_mountpoints()
    possible_mountpoints = [mount for mount in mounts if mount in folder.parents or mount == folder]
    if not possible_mountpoints:
        return  # Path does not exists, ex: disconnected USB drive

    return max(possible_mountpoints, key=lambda path: len(path.parts))


@lru_cache
def get_available_partitions() -> MappingProxyType[Path, NamedTuple]:
    """NOTE: This function is cached which means it always returns the partitions available at startup"""

    # all=True is required to make sure it works on most platforms. See: https://github.com/giampaolo/psutil/issues/2191
    partitions = psutil.disk_partitions(all=True)
    return MappingProxyType({Path(p.mountpoint): p for p in partitions})


@lru_cache
def get_available_mountpoints() -> tuple[Path, ...]:
    """NOTE: This function is cached which means it always returns the mounts available at startup"""
    return tuple(sorted(get_available_partitions().keys()))
