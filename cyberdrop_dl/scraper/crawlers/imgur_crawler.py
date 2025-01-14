from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from aiolimiter import AsyncLimiter
from yarl import URL

from cyberdrop_dl.clients.errors import LoginError, MaxChildrenError, ScrapeError
from cyberdrop_dl.scraper.crawler import Crawler
from cyberdrop_dl.utils.data_enums_classes.url_objects import FILE_HOST_ALBUM, ScrapeItem
from cyberdrop_dl.utils.logger import log
from cyberdrop_dl.utils.utilities import error_handling_wrapper, get_filename_and_ext

if TYPE_CHECKING:
    from cyberdrop_dl.managers.manager import Manager


class ImgurCrawler(Crawler):
    primary_base_domain = URL("https://imgur.com/")

    def __init__(self, manager: Manager) -> None:
        super().__init__(manager, "imgur", "Imgur")
        self.imgur_api = URL("https://api.imgur.com/3/")
        self.imgur_client_id = self.manager.config_manager.authentication_data.imgur.client_id
        self.imgur_client_remaining = 12500
        self.headers = {"Authorization": f"Client-ID {self.imgur_client_id}"}
        self.request_limiter = AsyncLimiter(10, 1)

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    async def fetch(self, scrape_item: ScrapeItem) -> None:
        """Determines where to send the scrape item based on the url."""
        task_id = self.scraping_progress.add_task(scrape_item.url)

        if "i.imgur.com" in scrape_item.url.host:
            await self.handle_direct(scrape_item)
        elif "a" in scrape_item.url.parts:
            await self.album(scrape_item)
        else:
            await self.image(scrape_item)

        self.scraping_progress.remove_task(task_id)

    @error_handling_wrapper
    async def album(self, scrape_item: ScrapeItem) -> None:
        """Scrapes an album."""
        if self.imgur_client_id == "":
            log("To scrape imgur content, you need to provide a client id", 30)
            raise LoginError(message="No Imgur Client ID provided")
        await self.check_imgur_credits(scrape_item)
        scrape_item.type = FILE_HOST_ALBUM
        scrape_item.children = scrape_item.children_limit = 0

        with contextlib.suppress(IndexError, TypeError):
            scrape_item.children_limit = (
                self.manager.config_manager.settings_data.download_options.maximum_number_of_children[scrape_item.type]
            )

        album_id = scrape_item.url.parts[-1]
        scrape_item.album_id = album_id
        scrape_item.part_of_album = True

        async with self.request_limiter:
            JSON_Obj = await self.client.get_json(
                self.domain,
                self.imgur_api / f"album/{album_id}",
                headers_inc=self.headers,
                origin=scrape_item,
            )
        title_part = JSON_Obj["data"].get("title", album_id)
        title = self.create_title(title_part, scrape_item.url.parts[2], None)

        async with self.request_limiter:
            JSON_Obj = await self.client.get_json(
                self.domain,
                self.imgur_api / f"album/{album_id}/images",
                headers_inc=self.headers,
                origin=scrape_item,
            )

        for image in JSON_Obj["data"]:
            link = URL(image["link"])
            date = image["datetime"]
            new_scrape_item = self.create_scrape_item(
                scrape_item,
                link,
                title,
                True,
                date,
                add_parent=scrape_item.url,
            )
            await self.handle_direct(new_scrape_item)
            scrape_item.children += 1
            if scrape_item.children_limit and scrape_item.children >= scrape_item.children_limit:
                raise MaxChildrenError(origin=scrape_item)

    @error_handling_wrapper
    async def image(self, scrape_item: ScrapeItem) -> None:
        """Scrapes an image."""
        if self.imgur_client_id == "":
            log("To scrape imgur content, you need to provide a client id", 30)
            raise LoginError(message="No Imgur Client ID provided")
        await self.check_imgur_credits(scrape_item)

        image_id = scrape_item.url.parts[-1]
        async with self.request_limiter:
            JSON_Obj = await self.client.get_json(
                self.domain,
                self.imgur_api / f"image/{image_id}",
                headers_inc=self.headers,
                origin=scrape_item,
            )

        date = JSON_Obj["data"]["datetime"]
        link = URL(JSON_Obj["data"]["link"])
        new_scrape_item = self.create_scrape_item(scrape_item, link, "", True, date)
        await self.handle_direct(new_scrape_item)

    @error_handling_wrapper
    async def handle_direct(self, scrape_item: ScrapeItem) -> None:
        """Scrapes an image."""
        filename, ext = get_filename_and_ext(scrape_item.url.name)
        if ext.lower() == ".gifv" or ext.lower() == ".mp4":
            filename = filename.replace(ext, ".mp4")
            ext = ".mp4"
            scrape_item.url = URL("https://imgur.com/download") / filename.replace(ext, "")
        await self.handle_file(scrape_item.url, scrape_item, filename, ext)

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    async def check_imgur_credits(self, scrape_item: ScrapeItem | None = None) -> None:
        """Checks the remaining credits."""
        credits_obj = await self.client.get_json(
            self.domain, self.imgur_api / "credits", headers_inc=self.headers, origin=scrape_item
        )
        self.imgur_client_remaining = credits_obj["data"]["ClientRemaining"]
        if self.imgur_client_remaining < 100:
            raise ScrapeError(429, "Imgur API rate limit reached", origin=scrape_item)
