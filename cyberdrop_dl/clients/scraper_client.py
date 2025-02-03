from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from aiohttp_client_cache.response import CachedStreamReader
from bs4 import BeautifulSoup

from cyberdrop_dl.clients.errors import DDOSGuardError, InvalidContentTypeError
from cyberdrop_dl.managers.client_manager import create_session

from .request_client import Client

if TYPE_CHECKING:
    import aiohttp
    from aiohttp_client_cache.session import CachedSession
    from multidict import CIMultiDictProxy
    from yarl import URL

    from cyberdrop_dl.utils.data_enums_classes.url_objects import ScrapeItem


@asynccontextmanager
async def cache_control_manager(client_session: CachedSession, disabled: bool = False):
    client_session.cache.disabled = disabled
    yield
    client_session.cache.disabled = False


class ScraperClient(Client):
    """AIOHTTP operations for scraping."""

    def __init__(self, client_manager: ClientManager) -> None:
        self.client_manager = client_manager
        self._headers = {"user-agent": client_manager.user_agent}
        self.trace_configs = []
        self.add_request_log_hooks()

    def add_request_log_hooks(self) -> None:
        async def on_request_start(*args):
            params: aiohttp.TraceRequestStartParams = args[2]
            log_debug(f"Starting scrape {params.method} request to {params.url}", 10)

        async def on_request_end(*args):
            params: aiohttp.TraceRequestEndParams = args[2]
            msg = f"Finishing scrape {params.method} request to {params.url}"
            msg += f" -> response status: {params.response.status}"
            log_debug(msg, 10)

        trace_config = aiohttp.TraceConfig()
        trace_config.on_request_start.append(on_request_start)
        trace_config.on_request_end.append(on_request_end)
        self.trace_configs.append(trace_config)

    @create_session
    async def get_soup(
        self,
        domain: str,
        url: URL,
        client_session: CachedSession,
        origin: ScrapeItem | URL | None = None,
        with_response_url: bool = False,
        cache_disabled: bool = False,
        retry: bool = True,
    ) -> tuple[BeautifulSoup, URL] | BeautifulSoup:
        """Returns a BeautifulSoup object from the given URL."""
        async with (
            cache_control_manager(client_session, disabled=cache_disabled),
            client_session.get(
                url, headers=self._headers, ssl=self.client_manager.ssl_context, proxy=self.client_manager.proxy
            ) as response,
        ):
            try:
                await self.client_manager.check_http_status(response, origin=origin)
            except DDOSGuardError:
                await self.client_manager.manager.cache_manager.request_cache.delete_url(url)
                soup, response_URL = await self.client_manager.flaresolverr.get(
                    url,
                    client_session,
                    origin,
                )
                # retry request with flaresolverr cookies
                if self.client_manager.check_ddos_guard(soup) or self.client_manager.check_cloudflare(soup):
                    if not retry:
                        raise DDOSGuardError(message="Unable to access website with flaresolverr cookies") from None
                    return await self.get_soup(
                        domain, url, client_session, origin, with_response_url, retry=False, cache_disabled=True
                    )
                if with_response_url:
                    return soup, response_URL
                return soup

            content_type = response.headers.get("Content-Type")
            assert content_type is not None
            if not any(s in content_type.lower() for s in ("html", "text")):
                raise InvalidContentTypeError(message=f"Received {content_type}, was expecting text", origin=origin)
            text = await CachedStreamReader(await response.read()).read()
            if with_response_url:
                return BeautifulSoup(text, "html.parser"), response.url
            return BeautifulSoup(text, "html.parser")

    async def get_soup_and_return_url(
        self, domain: str, url: URL, origin: ScrapeItem | URL | None = None, **kwargs
    ) -> tuple[BeautifulSoup, URL]:
        """Returns a BeautifulSoup object and response URL from the given URL."""
        return await self.get_soup(domain, url, origin=origin, with_response_url=True, **kwargs)

    @create_session
    async def get_json(
        self,
        domain: str,
        url: URL,
        client_session: CachedSession,
        params: dict | None = None,
        headers_inc: dict | None = None,
        origin: ScrapeItem | URL | None = None,
        cache_disabled: bool = False,
    ) -> tuple[dict, aiohttp.ClientResponse] | dict:
        """Returns a JSON object from the given URL."""
        headers = self._headers | headers_inc if headers_inc else self._headers
        async with (
            cache_control_manager(client_session, disabled=cache_disabled),
            client_session.get(
                url,
                headers=headers,
                ssl=self.client_manager.ssl_context,
                proxy=self.client_manager.proxy,
                params=params,
            ) as response,
        ):
            await self.client_manager.check_http_status(response, origin=origin)
            content_type = response.headers.get("Content-Type")
            assert content_type is not None
            if "json" not in content_type.lower():
                raise InvalidContentTypeError(message=f"Received {content_type}, was expecting JSON", origin=origin)
            json_resp = await response.json()
            if cache_disabled:
                return json_resp, response
            return json_resp

    @create_session
    async def get_text(
        self,
        domain: str,
        url: URL,
        client_session: CachedSession,
        origin: ScrapeItem | URL | None = None,
        cache_disabled: bool = False,
        retry: bool = True,
    ) -> str:
        """Returns a text object from the given URL."""
        async with (
            cache_control_manager(client_session, disabled=cache_disabled),
            client_session.get(
                url, headers=self._headers, ssl=self.client_manager.ssl_context, proxy=self.client_manager.proxy
            ) as response,
        ):
            try:
                await self.client_manager.check_http_status(response, origin=origin)
            except DDOSGuardError:
                await self.client_manager.manager.cache_manager.request_cache.delete_url(url)
                soup, _ = await self.client_manager.flaresolverr.get(url, client_session, origin)
                if self.client_manager.check_ddos_guard(soup) or self.client_manager.check_cloudflare(soup):
                    if not retry:
                        raise DDOSGuardError(message="Unable to access website with flaresolverr cookies") from None
                    return await self.get_text(domain, url, client_session, origin, retry=False, cache_disabled=True)
                return str(soup)
            return await response.text()

    @create_session
    async def post_data(
        self,
        domain: str,
        url: URL,
        client_session: CachedSession,
        data: dict,
        req_resp: bool = True,
        raw: bool = False,
        origin: ScrapeItem | URL | None = None,
        cache_disabled: bool = False,
        headers_inc: dict | None = None,
    ) -> dict | bytes:
        """Returns a JSON object from the given URL when posting data. If raw == True, returns raw binary data of response."""
        headers = self._headers | headers_inc if headers_inc else self._headers
        async with (
            cache_control_manager(client_session, disabled=cache_disabled),
            client_session.post(
                url,
                headers=headers,
                ssl=self.client_manager.ssl_context,
                proxy=self.client_manager.proxy,
                data=data,
            ) as response,
        ):
            await self.client_manager.check_http_status(response, origin=origin)
            if req_resp:
                content = await response.content.read()
                if content == b"":
                    content = await CachedStreamReader(await response.read()).read()
                return content if raw else json.loads(content)
            return {}

    @create_session
    async def get_head(
        self, domain: str, url: URL, client_session: CachedSession, *, origin: ScrapeItem | URL | None = None
    ) -> CIMultiDictProxy[str]:
        """Returns the headers from the given URL."""
        async with client_session.head(
            url,
            headers=self._headers,
            ssl=self.client_manager.ssl_context,
            proxy=self.client_manager.proxy,
        ) as response:
            await self.client_manager.check_http_status(response, origin=origin)
            return response.headers
