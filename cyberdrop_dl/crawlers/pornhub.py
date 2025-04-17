from __future__ import annotations

from typing import TYPE_CHECKING

from yarl import URL

from cyberdrop_dl.crawlers._yt_dlp import YtDlpCrawler
from cyberdrop_dl.crawlers.crawler import create_task_id
from cyberdrop_dl.utils.utilities import error_handling_wrapper

if TYPE_CHECKING:
    from cyberdrop_dl.managers.manager import Manager
    from cyberdrop_dl.utils.data_enums_classes.url_objects import ScrapeItem


CDN_HOSTS = "litter.catbox.moe", "files.catbox.moe"


class PornHubCrawler(YtDlpCrawler):
    primary_base_domain = URL("https://www.pornhub.com/")
    YT_DLP_SAMPLE_URL = URL("https://www.pornhub.com/view_video.php?viewkey=67f2d48ca6e8d")

    def __init__(self, manager: Manager) -> None:
        super().__init__(manager, "pornhub", "PornHub")

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    @create_task_id
    async def fetch(self, scrape_item: ScrapeItem) -> None:
        """Determines where to send the scrape item based on the url."""
        info_dict = await self.extract_info(scrape_item)
        if not info_dict:
            raise ValueError
        return await self.proccess_info_dict(scrape_item, info_dict)

    @error_handling_wrapper
    async def proccess_info_dict(self, scrape_item: ScrapeItem, info_dict: dict):
        pass
