from __future__ import annotations

import asyncio
from functools import partialmethod
from typing import TYPE_CHECKING, TypedDict

from yt_dlp import YoutubeDL
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.utils import DownloadError as YtDlpDownloadError
from yt_dlp.utils import ExtractorError, GeoRestrictedError, UnsupportedError

from cyberdrop_dl.utils.logger import log, log_debug

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from yarl import URL
    from yt_dlp.extractor.common import InfoExtractor


EXTRACT_INFO_TIMEOUT = 100  # seconds
ALL_IE: set[type[InfoExtractor]] = set(gen_extractor_classes())
IE_EXTRACTORS = {IE for IE in ALL_IE if IE.__name__ != "GenericIE"}

DEBUG_PREFIX = "[debug] "


class YtDlpLogger:
    def debug(self, msg: str, level: int = 20) -> None:
        if msg.startswith(DEBUG_PREFIX):
            return log_debug(msg.removeprefix(DEBUG_PREFIX))
        log(msg, level)

    info = partialmethod(debug, level=20)
    warning = partialmethod(debug, level=30)
    error = partialmethod(debug, level=40)


DEFAULT_EXTRACT_OPTIONS = {
    "quiet": True,
    "extract_flat": False,
    "skip_download": True,
    "simulate": True,
    "logger": YtDlpLogger(),
}

FOLDER_DOMAIN = "yt-dlp"
YT_DLP_BANNED_HOST = {}


class Format(TypedDict):
    url: str
    format_id: str
    height: int
    protocol: str
    ext: str
    video_ext: str | None
    audio_ext: str | None
    resolution: str
    filesize_approx: int | None
    http_headers: dict[str, str]
    format: str


class InfoDict(TypedDict):
    id: str
    uploader: str
    uploader_id: str
    upload_date: int
    title: str
    thumbnail: str
    duration: int
    formats: list[Format]
    timestamp: int
    webpage_url: str
    original_url: str
    extractor: str
    extractor_key: str
    playlist: str | None
    playlist_index: int | None
    display_id: int
    fulltitle: str


class AsyncYoutubeDLP(YoutubeDL):
    def __init__(self, download_archive: Path | None = None, noplaylist: bool = False, **kwargs):
        """
        extract_flat:

        Whether to resolve and process url_results further
        * False:     Always process. Default for API
        * True:      Never process
        * 'in_playlist': Do not process inside playlist/multi_video
        * 'discard': Always process, but don't return the result
                    from inside playlist/multi_video
        * 'discard_in_playlist': Same as "discard", but only for
                    playlists (not multi_video). Default for CLI
        """

        params = {"download_archive": download_archive, "noplaylist": noplaylist} | kwargs
        super().__init__(params=params)

    @staticmethod
    def get_supported_extractors(url: URL) -> Generator[type[InfoExtractor]]:
        """Checks if an URL is supported without making any request"""
        yield from get_supported_extractors(url)

    @staticmethod
    def is_supported(url: URL) -> bool:
        """Checks if an URL is supported without making any request"""
        return bool(next(get_supported_extractors(url), False))

    async def async_extract_info(self, url: URL) -> InfoDict:
        url_as_str = str(url)
        try:
            task = asyncio.to_thread(self.extract_info, url_as_str, download=False)
            result = await asyncio.wait_for(task, timeout=EXTRACT_INFO_TIMEOUT)
            info = self.sanitize_info(result)
            assert isinstance(info, dict | list)
            if isinstance(info, dict):
                return info  # type: ignore
            if isinstance(info, list):
                return {"data": info}  # type: ignore
        except UnsupportedError:
            return {}
        except (YtDlpDownloadError, ExtractorError, GeoRestrictedError):
            # raise DownloadError("YT-DLP Error", e.msg) from e
            pass
        return {}

    def in_download_archive(self, info: InfoDict) -> bool:
        return super().in_download_archive(info)


def get_supported_extractors(url: URL) -> Generator[type[InfoExtractor]]:
    """Checks if an URL is supported without making any request"""
    str_url = str(url)
    if url.host and url.host not in YT_DLP_BANNED_HOST:
        for extractor in IE_EXTRACTORS:
            if extractor.suitable(str_url):
                yield extractor
