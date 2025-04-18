from __future__ import annotations

import calendar
import datetime
from typing import TYPE_CHECKING

from yarl import URL

from cyberdrop_dl.clients.errors import ScrapeError
from cyberdrop_dl.crawlers.crawler import Crawler, create_task_id
from cyberdrop_dl.utils.data_enums_classes.url_objects import FILE_HOST_ALBUM, ScrapeItem
from cyberdrop_dl.utils.utilities import error_handling_wrapper, get_text_between

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

    from cyberdrop_dl.managers.manager import Manager


CONTENT_SELECTOR = "div[class='box-grid ng-star-inserted'] a[class=boxInner]"
DATE_SELECTOR = 'div[class="posted ng-star-inserted"]'
VIDEO_SELECTOR = "video source"
IMAGE_SELECTOR = 'img[class*="img shadow-base"]'


class Rule34XYZCrawler(Crawler):
    primary_base_domain = URL("https://rule34.xyz")

    def __init__(self, manager: Manager) -> None:
        super().__init__(manager, "rule34.xyz", "Rule34XYZ")

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    @create_task_id
    async def fetch(self, scrape_item: ScrapeItem) -> None:
        """Determines where to send the scrape item based on the url."""
        if "post" in scrape_item.url.parts:
            return await self.file(scrape_item)
        await self.tag(scrape_item)

    @error_handling_wrapper
    async def tag(self, scrape_item: ScrapeItem) -> None:
        """Scrapes an album."""
        # This is broke. Needs fixing
        raise NotImplementedError
        async with self.request_limiter:
            soup: BeautifulSoup = await self.client.get_soup(self.domain, scrape_item.url)

        scrape_item.set_type(FILE_HOST_ALBUM, self.manager)
        scrape_item.part_of_album = True
        title = self.create_title(scrape_item.url.parts[1])

        content = soup.select(CONTENT_SELECTOR)
        if not content:
            return

        for file_page in content:
            link_str: str = file_page.get("href")
            link = self.parse_url(link_str)
            new_scrape_item = self.create_scrape_item(scrape_item, link, title, add_parent=scrape_item.url)
            self.manager.task_group.create_task(self.run(new_scrape_item))
            scrape_item.add_children()

        page = 2
        if len(scrape_item.url.parts) > 2:
            page = int(scrape_item.url.parts[-1])
        next_page = scrape_item.url.with_path("/") / scrape_item.url.parts[1] / "page" / f"{page + 1}"
        new_scrape_item = self.create_scrape_item(scrape_item, next_page)
        self.manager.task_group.create_task(self.run(new_scrape_item))

    @error_handling_wrapper
    async def file(self, scrape_item: ScrapeItem) -> None:
        """Scrapes an image."""
        async with self.request_limiter:
            soup: BeautifulSoup = await self.client.get_soup(self.domain, scrape_item.url)

        if date_tag := soup.select_one(DATE_SELECTOR):
            date_str = get_text_between(date_tag.text, "(", ")")
            scrape_item.possible_datetime = parse_datetime(date_str)

        media_tag = soup.select_one(VIDEO_SELECTOR) or soup.select_one(IMAGE_SELECTOR)
        if not media_tag:
            raise ScrapeError(422)

        link = self.parse_url(media_tag["src"])  # type: ignore
        filename, ext = self.get_filename_and_ext(link.name)
        await self.handle_file(link, scrape_item, filename, ext)

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""


def parse_datetime(date: str) -> int:
    """Parses a datetime string into a unix timestamp."""
    parsed_date = datetime.datetime.strptime(date, "%b %d, %Y, %I:%M:%S %p")
    return calendar.timegm(parsed_date.timetuple())
