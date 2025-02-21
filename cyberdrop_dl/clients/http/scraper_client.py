from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from aiohttp_client_cache.response import CachedStreamReader
from bs4 import BeautifulSoup

from cyberdrop_dl.clients.http import Client, check, create_session
from cyberdrop_dl.clients.http.responses import GetRequestResponse, JsonRequestResponse, PostRequestResponse
from cyberdrop_dl.exceptions import DDOSGuardError, InvalidContentTypeError

if TYPE_CHECKING:
    from aiohttp_client_cache.session import CachedSession
    from multidict import CIMultiDictProxy
    from yarl import URL


@asynccontextmanager
async def cache_control(client_session: CachedSession, disabled: bool = False):
    client_session.cache.disabled = disabled
    yield
    client_session.cache.disabled = False


class ScraperClient(Client):
    """AIOHTTP operations for scraping."""

    @create_session
    async def get_soup(
        self,
        url: URL,
        client_session: CachedSession,
        *,
        cache_disabled: bool = False,
        retry: bool = True,
        **session_kwargs,
    ) -> GetRequestResponse:
        """Returns a BeautifulSoup object from the given URL."""
        session_kwargs = self.request_params | session_kwargs
        async with (
            cache_control(client_session, disabled=cache_disabled),
            client_session.get(url, **session_kwargs) as response,
        ):
            try:
                await check.raise_for_http_status(response)
            except DDOSGuardError:
                await self.client_manager.manager.cache_manager.request_cache.delete_url(url)
                cdl_response = await self.client_manager.flaresolverr.get(url, client_session)
                if check.is_ddos_guard(cdl_response.soup):
                    if not retry:
                        raise DDOSGuardError(message="Unable to access website with flaresolverr cookies") from None
                    return await self.get_soup(url, client_session, retry=False, cache_disabled=True)

                return cdl_response

            content_type = response.headers.get("Content-Type")
            assert content_type is not None
            if not any(s in content_type.lower() for s in ("html", "text")):
                raise InvalidContentTypeError(message=f"Received {content_type}, was expecting text")
            text = await CachedStreamReader(await response.read()).read()
            soup = BeautifulSoup(text, "html.parser")
            return GetRequestResponse(response.url, response.headers, response, soup)

    @create_session
    async def get_json(
        self,
        url: URL,
        client_session: CachedSession,
        *,
        cache_disabled: bool = False,
        **session_kwargs,
    ) -> JsonRequestResponse:
        """Returns a JSON object from the given URL."""
        session_kwargs = self.request_params | session_kwargs
        async with (
            cache_control(client_session, disabled=cache_disabled),
            client_session.get(url, **session_kwargs) as response,
        ):
            await check.raise_for_http_status(response)
            content_type = response.headers.get("Content-Type")
            assert content_type is not None
            content_type = content_type.lower()
            json_resp: dict = {}
            if "text" in content_type:
                try:
                    json_resp = json.loads(await response.text())
                except json.JSONDecodeError:
                    pass

            if not json_resp and "json" not in content_type:
                raise InvalidContentTypeError(message=f"Received {content_type}, was expecting JSON")

            json_resp = json_resp or await response.json()
            return JsonRequestResponse(response.url, response.headers, response, json_resp)

    @create_session
    async def post_data(
        self,
        url: URL,
        client_session: CachedSession,
        data: dict,
        *,
        cache_disabled: bool = False,
        req_resp: bool = True,
        **session_kwargs,
    ) -> PostRequestResponse:
        """Returns a JSON object from the given URL when posting data. If raw == True, returns raw binary data of response."""
        session_kwargs = self.request_params | session_kwargs
        async with (
            cache_control(client_session, disabled=cache_disabled),
            client_session.post(url, **session_kwargs, data=data) as response,
        ):
            await check.raise_for_http_status(response)
            if not req_resp:
                return  # type: ignore
            content = await response.content.read()
            if content == b"":
                content = await CachedStreamReader(await response.read()).read()
            json_resp = json.loads(content)
            return PostRequestResponse(response.url, response.headers, response, json_resp, content)

    @create_session
    async def get_head(self, url: URL, client_session: CachedSession, **session_kwargs) -> CIMultiDictProxy[str]:
        """Returns the headers from the given URL."""
        session_kwargs = self.request_params | session_kwargs
        async with client_session.head(url, **session_kwargs) as response:
            await check.raise_for_http_status(response)
            return response.headers

    @create_session
    async def get_text(
        self,
        url: URL,
        client_session: CachedSession,
        *,
        cache_disabled: bool = False,
        _retry: bool = True,
        **session_kwargs,
    ) -> str:
        """Returns a text object from the given URL."""
        session_kwargs = self.request_params | session_kwargs
        async with (
            cache_control(client_session, disabled=cache_disabled),
            client_session.get(url, **session_kwargs) as response,
        ):
            try:
                await check.raise_for_http_status(response)
            except DDOSGuardError:
                await self.client_manager.manager.cache_manager.request_cache.delete_url(url)
                f_resp = await self.client_manager.flaresolverr.get(url, client_session)
                soup = f_resp.soup
                del f_resp
                if check.is_ddos_guard(soup):
                    if not _retry:
                        raise DDOSGuardError(message="Unable to access website with flaresolverr cookies") from None
                    return await self.get_text(url, client_session, _retry=False, cache_disabled=True)
                return str(soup)
            return await response.text()
