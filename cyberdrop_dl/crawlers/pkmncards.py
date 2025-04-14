from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, NewType

from yarl import URL

from cyberdrop_dl.clients.errors import ScrapeError
from cyberdrop_dl.crawlers.crawler import Crawler, create_task_id
from cyberdrop_dl.utils.utilities import error_handling_wrapper

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

    from cyberdrop_dl.managers.manager import Manager
    from cyberdrop_dl.utils.data_enums_classes.url_objects import ScrapeItem

CARD_DOWNLOAD_SELECTOR = "li > a[title='Download Image']"

TimeStamp = NewType("TimeStamp", int)


CARD_PAGE_TITLE_SELECTOR = "meta[property='og:title']"
SET_SERIES_CODE_SELECTOR = "div.card-tabs span[title='Set Series Code']"
SET_INFO_SELECTOR = "script:contains('datePublished')"


@dataclass(slots=True)
class CardSet:
    name: str
    abbr: str
    set_series_code: str | None
    release_date: TimeStamp

    @property
    def full_code(self) -> str:
        if self.set_series_code:
            return f"{self.abbr}, {self.set_series_code}"
        return f"{self.abbr}"


# This is just for information about what properties the card has. We don't actually use this class
@dataclass(slots=True)
class Card:
    name: str
    number_str: str
    set: CardSet
    hp: int = 0
    color: str = ""
    type: str = ""
    text: str = ""
    pokemons: tuple[str, ...] = ()
    simbols: tuple[str, ...] = ()
    ram: int = 0
    rarity: str = ""


@dataclass(slots=True)
class SimpleCard:
    # Simplified version of Card that groups the information we can get from the title of a page
    name: str | None
    number_str: str  # This can actually contain letters as well, but the oficial name is `number`
    set_name: str
    set_abbr: str

    @property
    def full_name(self) -> str:
        if self.name:
            return f"{self.name} ({self.set_abbr}) #{self.number_str}"
        else:
            return f"#{self.number_str}"


class PkmncardsCrawler(Crawler):
    primary_base_domain = URL("https://pkmncards.com")

    def __init__(self, manager: Manager) -> None:
        super().__init__(manager, "pkmncards", "Pkmncards")

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    @create_task_id
    async def fetch(self, scrape_item: ScrapeItem) -> None:
        """Determines where to send the scrape item based on the url."""
        n_parts = len(scrape_item.url.parts)
        if "card" in scrape_item.url.parts and n_parts > 2:
            return await self.card(scrape_item)

        # We can download from this URL but we can't get any metadata
        # It would be downloaded as a loose file with a random name, so i disabled it
        # if scrape_item.url.path.startswith("/wp-content/uploads/"):
        #    return await self.direct_file(scrape_item)
        raise ValueError

    @error_handling_wrapper
    async def card(self, scrape_item: ScrapeItem, card_set: CardSet | None = None) -> None:
        async with self.request_limiter:
            soup: BeautifulSoup = await self.client.get_soup(self.domain, scrape_item.url)

        link_str: str = soup.select_one(CARD_DOWNLOAD_SELECTOR)["href"]  # type: ignore
        link = self.parse_url(link_str)
        title: str = soup.select_one(CARD_PAGE_TITLE_SELECTOR)["content"]  # type: ignore
        card = parse_card_info_from_title(title)
        if not card_set:
            card_set = create_set(soup, card)

        set_title = self.create_title(f"{card_set.name} ({card_set.full_code})")
        scrape_item.setup_as_album(set_title, album_id=card_set.abbr)
        scrape_item.possible_datetime = card_set.release_date
        filename, ext = self.get_filename_and_ext(link.name, assume_ext=".jpg")
        custom_filename, _ = self.get_filename_and_ext(f"{card.full_name}{link.suffix}")
        await self.handle_file(link, scrape_item, filename, ext, custom_filename=custom_filename)


def parse_card_info_from_title(title: str) -> SimpleCard:
    """Over-complicated function to parse the information of a card from title of a page / alt-title of a thumbnail."""

    # ex: Fuecoco · Scarlet & Violet Promos (SVP) #002
    # ex: Sprigatito · Scarlet & Violet Promos (SVP) #001 ‹ PkmnCards  # noqa: RUF003
    # TODO: Replace with regex groups?

    clean_title = title.removesuffix("‹ PkmnCards").strip()  # noqa: RUF001
    _rest, card_number = clean_title.rsplit("#", 1)
    if clean_title.startswith("#"):
        # ex: #xy188 ‹ PkmnCards  # noqa: RUF003
        buffer = ""
        for char in reversed(card_number):
            if char.isdigit():
                buffer = char + buffer
            else:
                break
        set_name = card_number.removesuffix(buffer)
        return SimpleCard(None, card_number, set_name, set_name)

    card_name, set_details = _rest.split("·", 1)
    set_name, set_abbr = set_details.replace(")", "").rsplit("(", 1)
    return SimpleCard(card_name.strip(), card_number.strip(), set_name.strip(), set_abbr.strip().upper())


def create_set(soup: BeautifulSoup, card: SimpleCard) -> CardSet:
    tag = soup.select_one(SET_SERIES_CODE_SELECTOR)
    # Some sets do not have series code
    set_series_code: str | None = tag.get_text(strip=True) if tag else None  # type: ignore
    set_info: dict[str, list[dict]] = json.loads(soup.select_one(SET_INFO_SELECTOR).text)  # type: ignore
    release_date: int | None = None
    for item in set_info["@graph"]:
        if iso_date := item.get("datePublished"):
            release_date = calendar.timegm(datetime.fromisoformat(iso_date).timetuple())
            break

    if not release_date or not card.name:
        raise ScrapeError(422)

    return CardSet(card.set_name, card.set_abbr, set_series_code, TimeStamp(release_date))
