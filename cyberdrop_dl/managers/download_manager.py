from __future__ import annotations

import asyncio
from base64 import b64encode
from contextlib import asynccontextmanager
from shutil import disk_usage
from typing import TYPE_CHECKING

from cyberdrop_dl.clients.download_client import check_file_duration
from cyberdrop_dl.utils.constants import FILE_FORMATS
from cyberdrop_dl.utils.logger import log_debug

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from cyberdrop_dl.managers.manager import Manager
    from cyberdrop_dl.scraper.crawler import Crawler
    from cyberdrop_dl.utils.data_enums_classes.url_objects import MediaItem


class FileLocksVault:
    """Is this necessary? No. But I want it."""

    def __init__(self) -> None:
        self._locked_files: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def get_lock(self, filename: str) -> AsyncGenerator:
        """Get filelock for the provided filename. Creates one if none exists"""
        log_debug(f"Checking lock for {filename}", 20)
        if filename not in self._locked_files:
            log_debug(f"Lock for {filename} does not exists", 20)

        self._locked_files[filename] = self._locked_files.get(filename, asyncio.Lock())
        async with self._locked_files[filename]:
            log_debug(f"Lock for {filename} acquired", 20)
            yield
            log_debug(f"Lock for {filename} released", 20)


class DownloadManager:
    def __init__(self, manager: Manager) -> None:
        rate_limiting_options = manager.config_manager.global_settings_data.rate_limiting_options
        self.manager = manager
        self.file_locks = FileLocksVault()

        self.download_spacers = {}
        self.download_semaphores = {}
        self.max_slots_per_domain = rate_limiting_options.max_simultaneous_downloads_per_domain
        self.global_download_semaphore = asyncio.Semaphore(rate_limiting_options.max_simultaneous_downloads)
        self.global_download_delay = rate_limiting_options.download_delay
        self.default_semaphore = asyncio.Semaphore(self.max_slots_per_domain)

    def register(self, crawler: Crawler) -> None:
        domain = crawler.domain
        assert domain not in self.download_spacers, f"{domain} is already registered"
        self.download_spacers.update({domain: crawler.download_spacer})
        semaphore = self.default_semaphore
        if crawler.max_concurrent_downloads:
            semaphore = asyncio.Semaphore(crawler.max_concurrent_downloads)
        self.download_semaphores.update({domain: semaphore})

    def get_download_semaphore(self, domain: str) -> asyncio.Semaphore:
        """Returns the download limit for a domain."""
        return self.download_semaphores.get(domain, self.default_semaphore)

    @asynccontextmanager
    async def limiter(self, domain: str):
        download_spacer = self.download_spacers.get(domain, 0.1)
        await asyncio.sleep(self.global_download_delay + download_spacer)
        async with self.manager.client_manager.limiter(domain):
            yield

    @staticmethod
    def basic_auth(username: str, password: str) -> str:
        """Returns a basic auth token."""
        token = b64encode(f"{username}:{password}".encode()).decode("ascii")
        return f"Basic {token}"

    def check_free_space(self, folder: Path | None = None) -> bool:
        """Checks if there is enough free space on the drive to continue operating."""
        if not folder:
            folder = self.manager.path_manager.download_folder

        folder = folder.resolve()
        while not folder.is_dir() and folder.parents:
            folder = folder.parent

        if not folder.is_dir():
            return False
        free_space = disk_usage(folder).free
        return free_space >= self.manager.config_manager.global_settings_data.general.required_free_space

    def check_allowed_filetype(self, media_item: MediaItem) -> bool:
        """Checks if the file type is allowed to download."""
        ignore_options = self.manager.config_manager.settings_data.ignore_options
        valid_extensions = FILE_FORMATS["Images"] | FILE_FORMATS["Videos"] | FILE_FORMATS["Audio"]
        if media_item.ext.lower() in FILE_FORMATS["Images"] and ignore_options.exclude_images:
            return False
        if media_item.ext.lower() in FILE_FORMATS["Videos"] and ignore_options.exclude_videos:
            return False
        if media_item.ext.lower() in FILE_FORMATS["Audio"] and ignore_options.exclude_audio:
            return False
        return not (ignore_options.exclude_other and media_item.ext.lower() not in valid_extensions)

    def pre_check_duration(self, media_item: MediaItem) -> bool:
        """Checks if the download is above the maximum runtime."""
        if not media_item.duration:
            return True

        return check_file_duration(media_item, self.manager)
