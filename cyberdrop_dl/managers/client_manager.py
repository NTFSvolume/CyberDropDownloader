from __future__ import annotations

import asyncio
import contextlib
import ssl
from contextlib import asynccontextmanager
from functools import wraps
from dataclasses import dataclass
from http import HTTPStatus
from http.cookiejar import MozillaCookieJar
from typing import TYPE_CHECKING

import aiohttp
import certifi
from aiohttp import ClientResponse, ContentTypeError
from aiolimiter import AsyncLimiter
from bs4 import BeautifulSoup
from yarl import URL

from cyberdrop_dl.clients.download_client import DownloadClient
from cyberdrop_dl.clients.errors import DDOSGuardError, DownloadError, ScrapeError
from cyberdrop_dl.clients.scraper_client import ScraperClient
from cyberdrop_dl.managers.download_speed_manager import DownloadSpeedLimiter
from cyberdrop_dl.ui.prompts.user_prompts import get_cookies_from_browsers
from cyberdrop_dl.utils.constants import CustomHTTPStatus
from cyberdrop_dl.utils.logger import log, log_spacer

from .flaresolverr import Flaresolverr

if TYPE_CHECKING:
    from cyberdrop_dl.managers.manager import Manager
    from cyberdrop_dl.scraper.crawler import ScrapeItem

DOWNLOAD_ERROR_ETAGS = {
    "d835884373f4d6c8f24742ceabe74946": "Imgur image has been removed",
    "65b7753c-528a": "SC Scrape Image",
    "5c4fb843-ece": "PixHost Removed Image",
}


CLOUDFLARE_CHALLENGE_TITLES = ["Simpcity Cuck Detection", "Attention Required! | Cloudflare"]
CLOUDFLARE_CHALLENGE_SELECTORS = ["captchawrapper", "cf-turnstile"]
DDOS_GUARD_CHALLENGE_TITLES = ["Just a moment...", "DDoS-Guard"]
DDOS_GUARD_CHALLENGE_SELECTORS = [
    "#cf-challenge-running",
    ".ray_id",
    ".attack-box",
    "#cf-please-wait",
    "#challenge-spinner",
    "#trk_jschal_js",
    "#turnstile-wrapper",
    ".lds-ring",
]


class ClientManager:
    """Creates a 'client' that can be referenced by scraping or download sessions."""

    def __init__(self, manager: Manager) -> None:
        global_settings_data = manager.config_manager.global_settings_data
        rate_limiting_options = global_settings_data.rate_limiting_options
        verify_ssl = not global_settings_data.general.allow_insecure_connections
        read_timeout = rate_limiting_options.read_timeout
        connection_timeout = rate_limiting_options.connection_timeout
        total_timeout = read_timeout + connection_timeout

        self.manager = manager
        self.ssl_context = ssl.create_default_context(cafile=certifi.where()) if verify_ssl else False
        self.user_agent = global_settings_data.general.user_agent
        self.auto_import_cookies = self.manager.config_manager.settings_data.browser_cookies.auto_import
        self.cookies = aiohttp.CookieJar(quote_cookie=False)
        self.proxy: URL | None = global_settings_data.general.proxy  # type: ignore
        self.timeout = aiohttp.ClientTimeout(total=total_timeout, connect=connection_timeout)

        self.request_limiters = {
            "bunkrr": AsyncLimiter(5, 1),
            "cyberdrop": AsyncLimiter(5, 1),
            "coomer": AsyncLimiter(1, 1),
            "kemono": AsyncLimiter(1, 1),
            "pixeldrain": AsyncLimiter(10, 1),
            "gofile": AsyncLimiter(100, 60),
            "other": AsyncLimiter(25, 1),
        }

        self.download_spacer = {
            "bunkr": 0.5,
            "bunkrr": 0.5,
            "cyberdrop": 0,
            "cyberfile": 0,
            "pixeldrain": 0,
            "coomer": 0.5,
            "kemono": 0.5,
        }

        self.global_request_limiter = AsyncLimiter(rate_limiting_options.rate_limit, 1)
        self.global_request_semaphore = asyncio.Semaphore(50)
        self.global_download_semaphore = asyncio.Semaphore(rate_limiting_options.max_simultaneous_downloads)
        self.global_download_delay = rate_limiting_options.download_delay

        self.scraper_client = ScraperClient(manager)
        self.downloader_client = DownloadClient(manager)
        self.download_speed_limiter = DownloadSpeedLimiter(manager)
        self.flaresolverr = Flaresolverr(manager)

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    def register_limiter(self, domain: str, limiter: AsyncLimiter) -> None:
        assert domain not in self.request_limiters
        self.request_limiters.update({domain: limiter})

    def get_download_spacer(self, key: str) -> float:
        """Returns the download spacer for a domain."""
        return self.download_spacer.get(key, 0.1)

    def get_request_limiter(self, domain: str) -> AsyncLimiter:
        """Get a rate limiter for a domain."""
        default = self.request_limiters["other"]
        return self.download_spacer.get(domain, default)

    @asynccontextmanager
    async def limiter(self, domain: str):
        domain_request_limiter = self.get_request_limiter(domain)
        async with (
            self.global_request_semaphore,
            self.global_request_limiter,
            domain_request_limiter,
        ):
            yield

    @asynccontextmanager
    async def download_limiter(self, domain: str):
        download_spacer = self.get_download_spacer(domain)
        await asyncio.sleep(self.global_download_delay + download_spacer)
        async with self.limiter(domain):
            yield

    async def close(self) -> None:
        await self.flaresolverr._destroy_session()

    """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"""

    @staticmethod
    def check_bunkr_maint(headers: dict):
        if headers.get("Content-Length") == "322509" and headers.get("Content-Type") == "video/mp4":
            raise DownloadError(status="Bunkr Maintenance", message="Bunkr under maintenance")

    @staticmethod
    def check_ddos_guard(soup: BeautifulSoup) -> bool:
        return check_soup(soup, DDOS_GUARD_CHALLENGE_TITLES, DDOS_GUARD_CHALLENGE_SELECTORS)

    @staticmethod
    def check_cloudflare(soup: BeautifulSoup) -> bool:
        return check_soup(soup, CLOUDFLARE_CHALLENGE_TITLES, CLOUDFLARE_CHALLENGE_SELECTORS)

    def load_cookie_files(self) -> None:
        if self.auto_import_cookies:
            get_cookies_from_browsers(self.manager)
        cookie_files = sorted(self.manager.path_manager.cookies_dir.glob("*.txt"))
        if not cookie_files:
            return

        domains_seen = set()
        for file in cookie_files:
            cookie_jar = MozillaCookieJar(file)
            try:
                cookie_jar.load(ignore_discard=True)
            except OSError as e:
                log(f"Unable to load cookies from '{file.name}':\n  {e!s}", 40)
                continue
            current_cookie_file_domains = set()
            for cookie in cookie_jar:
                simplified_domain = cookie.domain.removeprefix(".")
                if simplified_domain not in current_cookie_file_domains:
                    log(f"Found cookies for {simplified_domain} in file '{file.name}'", 20)
                    current_cookie_file_domains.add(simplified_domain)
                    if simplified_domain in domains_seen:
                        log(f"Previous cookies for domain {simplified_domain} detected. They will be overwritten", 30)
                domains_seen.add(simplified_domain)
                self.cookies.update_cookies({cookie.name: cookie.value}, response_url=URL(f"https://{cookie.domain}"))  # type: ignore

        log_spacer(20, log_to_console=False)

    @classmethod
    async def check_http_status(
        cls, response: ClientResponse, download: bool = False, origin: ScrapeItem | URL | None = None
    ) -> None:
        """Checks the HTTP status code and raises an exception if it's not acceptable."""
        status = response.status
        headers = response.headers

        e_tag = headers.get("ETag")
        if download and e_tag and e_tag in DOWNLOAD_ERROR_ETAGS:
            message = DOWNLOAD_ERROR_ETAGS.get(e_tag)
            raise DownloadError(HTTPStatus.NOT_FOUND, message=message, origin=origin)

        if HTTPStatus.OK <= status < HTTPStatus.BAD_REQUEST:
            return

        assert response.url.host
        if any(domain in response.url.host for domain in ("gofile", "imgur")):
            with contextlib.suppress(ContentTypeError):
                JSON_Resp: dict = await response.json()
                status_str: str = JSON_Resp.get("status")  # type: ignore
                if status_str and isinstance(status, str) and "notFound" in status_str:
                    raise ScrapeError(404, origin=origin)
                data = JSON_Resp.get("data")
                if data and isinstance(data, dict) and "error" in data:
                    raise ScrapeError(status_str, data["error"], origin=origin)

        response_text = None
        with contextlib.suppress(UnicodeDecodeError):
            response_text = await response.text()

        if response_text:
            soup = BeautifulSoup(response_text, "html.parser")
            if cls.check_ddos_guard(soup) or cls.check_cloudflare(soup):
                raise DDOSGuardError(origin=origin)
        status: str | int = status if headers.get("Content-Type") else CustomHTTPStatus.IM_A_TEAPOT
        message = None if headers.get("Content-Type") else "No content-type in response header"

        raise DownloadError(status=status, message=message, origin=origin)

    @staticmethod
    def check_bunkr_maint(headers: dict):
        if headers.get("Content-Length") == "322509" and headers.get("Content-Type") == "video/mp4":
            raise DownloadError(status="Bunkr Maintenance", message="Bunkr under maintenance")

    @staticmethod
    def check_ddos_guard(soup: BeautifulSoup) -> bool:
        return check_soup(soup, DDOS_GUARD_CHALLENGE_TITLES, DDOS_GUARD_CHALLENGE_SELECTORS)

    @staticmethod
    def check_cloudflare(soup: BeautifulSoup) -> bool:
        return check_soup(soup, CLOUDFLARE_CHALLENGE_TITLES, CLOUDFLARE_CHALLENGE_SELECTORS)

    async def close(self) -> None:
        await self.flaresolverr._destroy_session()

    @asynccontextmanager
    async def limiter(self, domain: str):
        domain_request_limiter = self.get_request_limiter(domain)
        async with (
            self.global_request_semaphore,
            self.global_request_limiter,
            domain_request_limiter,
        ):
            yield

    @asynccontextmanager
    async def download_limiter(self, domain: str):
        download_spacer = self.get_download_spacer(domain)
        await asyncio.sleep(self.global_download_delay + download_spacer)
        async with self.limiter(domain):
            yield


@dataclass(frozen=True, slots=True)
class FlaresolverrResponse:
    status: str
    cookies: dict
    user_agent: str
    soup: BeautifulSoup
    url: URL

    @classmethod
    def from_dict(cls, flaresolverr_resp: dict) -> FlaresolverrResponse:
        status = flaresolverr_resp["status"]
        solution: dict = flaresolverr_resp["solution"]
        response = solution["response"]
        user_agent = solution["userAgent"].strip()
        url_str: str = solution["url"]
        cookies: dict = solution.get("cookies") or {}
        soup = BeautifulSoup(response, "html.parser")
        url = URL(url_str)
        return cls(status, cookies, user_agent, soup, url)


class Flaresolverr:
    """Class that handles communication with flaresolverr."""

    def __init__(self, client_manager: ClientManager) -> None:
        self.client_manager = client_manager
        self.flaresolverr_host = client_manager.manager.config_manager.global_settings_data.general.flaresolverr
        self.enabled = bool(self.flaresolverr_host)
        self.session_id = None
        self.timeout = aiohttp.ClientTimeout(total=120000, connect=60000)

    async def _request(
        self,
        command: str,
        client_session: ClientSession,
        origin: ScrapeItem | URL | None = None,
        **kwargs,
    ) -> dict:
        """Base request function to call flaresolverr."""
        if not self.enabled:
            raise DDOSGuardError(message="FlareSolverr is not configured", origin=origin)

        if not (self.session_id or kwargs.get("session")):
            await self._create_session()

        headers = client_session.headers.copy()
        headers.update({"Content-Type": "application/json"})
        for key, value in kwargs.items():
            if isinstance(value, URL):
                kwargs[key] = str(value)

        data = {"cmd": command, "maxTimeout": 60000, "session": self.session_id} | kwargs

        async with client_session.post(
            self.flaresolverr_host / "v1",
            headers=headers,
            ssl=self.client_manager.ssl_context,
            proxy=self.client_manager.proxy,
            json=data,
            timeout=self.timeout,
        ) as response:
            json_obj: dict = await response.json()  # type: ignore

        return json_obj

    async def _create_session(self) -> None:
        """Creates a permanet flaresolverr session."""
        session_id = "cyberdrop-dl"
        async with ClientSession() as client_session:
            flaresolverr_resp = await self._request("sessions.create", client_session, session=session_id)
        status = flaresolverr_resp.get("status")
        if status != "ok":
            raise DDOSGuardError(message="Failed to create flaresolverr session")
        self.session_id = session_id

    async def _destroy_session(self):
        if self.session_id:
            async with ClientSession() as client_session:
                await self._request("sessions.destroy", client_session, session=self.session_id)

    async def get(
        self,
        url: URL,
        client_session: ClientSession,
        origin: ScrapeItem | URL | None = None,
        update_cookies: bool = True,
    ) -> tuple[BeautifulSoup, URL]:
        """Returns the resolved URL from the given URL."""
        json_resp: dict = await self._request("request.get", client_session, origin, url=url)

        try:
            fs_resp = FlaresolverrResponse.from_dict(json_resp)
        except (AttributeError, KeyError):
            raise DDOSGuardError(message="Invalid response from flaresolverr", origin=origin) from None

        if fs_resp.status != "ok":
            raise DDOSGuardError(message="Failed to resolve URL with flaresolverr", origin=origin)

        mismatch_msg = f"Config user_agent and flaresolverr user_agent do not match: \n  Cyberdrop-DL: {fs_resp.user_agent}\n  Flaresolverr: {fs_resp.user_agent}"

        user_agent = client_session.headers["User-Agent"].strip()
        if self.client_manager.check_ddos_guard(fs_resp.soup) or self.client_manager.check_cloudflare(fs_resp.soup):
            if not update_cookies:
                raise DDOSGuardError(message="Invalid response from flaresolverr", origin=origin)
            if fs_resp.user_agent != user_agent:
                raise DDOSGuardError(message=mismatch_msg, origin=origin)

        if update_cookies:
            if fs_resp.user_agent != user_agent:
                log(f"{mismatch_msg}\nResponse was successful but cookies will not be valid", 30)

            for cookie in fs_resp.cookies:
                self.client_manager.cookies.update_cookies(
                    {cookie["name"]: cookie["value"]}, URL(f"https://{cookie['domain']}")
                )

        return fs_resp.soup, fs_resp.url


def create_session(func: Callable) -> Any:
    """Wrapper handles client session creation to pass cookies."""

    @wraps(func)
    async def wrapper(self: DownloadClient | ScraperClient, *args, **kwargs):
        async with aiohttp.ClientSession(
            headers=self._headers,
            cookie_jar=self.client_manager.cookies,
            timeout=self.client_manager.timeout,
            trace_configs=self.trace_configs,
        ) as client:
            kwargs["client_session"] = client
            return await func(self, *args, **kwargs)

    return wrapper


def check_soup(soup: BeautifulSoup, titles: list[str], selectors: list[str]) -> bool:
    if soup.title:
        for title in titles:
            challenge_found = title.casefold() == soup.title.text.casefold()
            if challenge_found:
                return True

    for selector in selectors:
        challenge_found = soup.find(selector)
        if challenge_found:
            return True

    return False
