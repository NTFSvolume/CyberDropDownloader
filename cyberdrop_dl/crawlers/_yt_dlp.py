from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from cyberdrop_dl.crawlers.crawler import Crawler, create_task_id
from cyberdrop_dl.utils.utilities import error_handling_wrapper
from cyberdrop_dl.utils.yt_dlp import AsyncYoutubeDLP, InfoDict

if TYPE_CHECKING:
    from yarl import URL

    from cyberdrop_dl.managers.manager import Manager
    from cyberdrop_dl.utils.data_enums_classes.url_objects import ScrapeItem


class YtDlpCrawler(Crawler):
    YT_DLP_SAMPLE_URL: URL = None  # type: ignore

    def __init__(self, manager: Manager, domain: str, folder_domain: str | None = None, **kwargs) -> None:
        super().__init__(manager, domain, folder_domain)
        self.yt_dlp = AsyncYoutubeDLP(**kwargs)
        self._supported_extractors = tuple(self.yt_dlp.get_supported_extractors(self.YT_DLP_SAMPLE_URL))
        assert self._supported_extractors, "Sample URL is not valid. Did not find any compatible extractor"
        self.extractor = self._supported_extractors[0]()
        pass

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    @create_task_id
    async def fetch(self, scrape_item: ScrapeItem) -> None:
        """Determines where to send the scrape item based on the url."""
        info_dict = await self.extract_info(scrape_item)
        return await self.proccess_info_dict(scrape_item, info_dict)

    async def extract_info(self, scrape_item: ScrapeItem) -> InfoDict:
        if self.extractor.suitable(str(scrape_item.url)):
            info = await self.__get_info(scrape_item)
            if info:
                return info
        raise ValueError

    @error_handling_wrapper
    async def __get_info(self, scrape_item: ScrapeItem):
        return await self.yt_dlp.async_extract_info(scrape_item.url)

    @abstractmethod
    async def proccess_info_dict(self, scrape_item: ScrapeItem, info_dict: InfoDict): ...
