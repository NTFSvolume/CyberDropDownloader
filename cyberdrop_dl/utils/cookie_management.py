from __future__ import annotations

import asyncio
import functools
import os
import socket
import webbrowser
from http.cookiejar import MozillaCookieJar
from textwrap import dedent
from typing import TYPE_CHECKING

import browser_cookie3
from multidict import CIMultiDict

from cyberdrop_dl.utils import constants

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from cyberdrop_dl.managers.manager import Manager


COOKIE_ERROR_FOOTER = "\n\nNothing has been saved."
CHROMIUM_BROWSERS = ["chrome", "chromium", "opera", "opera_gx", "brave", "edge", "vivaldi", "arc"]


class UnsupportedBrowserError(browser_cookie3.BrowserCookieError): ...


def cookie_extraction_error_wrapper(func: Callable) -> Callable:
    """Wrapper handles errors for cookie extraction."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> None:
        try:
            return func(*args, **kwargs)
        except PermissionError as e:
            msg = """We've encountered a Permissions Error. Please close all browsers and try again
                     If you are still having issues, make sure all browsers processes are closed in Task Manager"""
            msg = dedent(msg) + f"\nERROR: {e!s}"

        except (ValueError, UnsupportedBrowserError) as e:
            msg = f"ERROR: {e!s}"

        except browser_cookie3.BrowserCookieError as e:
            msg = """Browser extraction ran into an error, the selected browser(s) may not be available on your system
                     If you are still having issues, make sure all browsers processes are closed in Task Manager."""
            msg = dedent(msg) + f"\nERROR: {e!s}"

        raise browser_cookie3.BrowserCookieError(msg + COOKIE_ERROR_FOOTER)

    return wrapper


@cookie_extraction_error_wrapper
def get_cookies_from_browsers(
    manager: Manager, *, browsers: list[constants.BROWSERS] | list[str] | None = None, domains: list[str] | None = None
) -> None:
    if browsers == []:
        msg = "No browser selected"
        raise ValueError(msg)
    if domains == []:
        msg = "No domains selected"
        raise ValueError(msg)

    browsers = browsers or manager.config_manager.settings_data.browser_cookies.browsers
    browsers = list(map(str.lower, browsers))
    domains = domains or manager.config_manager.settings_data.browser_cookies.sites
    extractors = [(b, getattr(browser_cookie3, b)) for b in browsers if hasattr(browser_cookie3, b)]

    if not extractors:
        msg = "None of the provided browsers is supported for extraction"
        raise ValueError(msg)

    def check_unsupported_browser(error: browser_cookie3.BrowserCookieError, extractor_name: str) -> None:
        msg = str(error)
        is_decrypt_error = "Unable to get key for cookie decryption" in msg
        if is_decrypt_error and extractor_name in CHROMIUM_BROWSERS and os.name == "nt":
            msg = f"Cookie extraction from {extractor_name.capitalize()} is not supported on Windows. Use a Firefox based browser - {msg}"
            raise UnsupportedBrowserError(msg)

    manager.path_manager.cookies_dir.mkdir(parents=True, exist_ok=True)
    for domain in domains:
        cookie_jar = MozillaCookieJar()
        for extractor_name, extractor in extractors:
            try:
                cookies = extractor(domain_name=domain)
            except browser_cookie3.BrowserCookieError as e:
                check_unsupported_browser(e, extractor_name)
                raise
            for cookie in cookies:
                cookie_jar.set_cookie(cookie)
            cookie_file_path = manager.path_manager.cookies_dir / f"{domain}.txt"
        cookie_jar.save(cookie_file_path, ignore_discard=True, ignore_expires=True)  # type: ignore


def clear_cookies(manager: Manager, domains: list[str]) -> None:
    if domains == []:
        raise ValueError("No domains selected")

    manager.path_manager.cookies_dir.mkdir(parents=True, exist_ok=True)
    for domain in domains:
        cookie_jar = MozillaCookieJar()
        cookie_file_path = manager.path_manager.cookies_dir / f"{domain}.txt"
        cookie_jar.save(cookie_file_path, ignore_discard=True, ignore_expires=True)  # type: ignore


async def get_browser_user_agent(browser: str | None = None) -> str | None:
    """Get User-Agent header from browser."""

    try:
        web_browser = webbrowser.get(browser)
    except webbrowser.Error:
        if not browser:
            raise
        msg = f"Unable to open browser '{browser}' (not installed or executable path not found)"
        try:
            available_browsers: list[str] | None = webbrowser._tryorder  # type: ignore
            if available_browsers:
                msg += f"\nInstalled browsers: {available_browsers}"
        except AttributeError:
            pass
        raise webbrowser.Error(msg) from None

    loop = asyncio.get_running_loop()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # IPV4, TCP
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    url = f"http://{host}:{port}/cyberdrop-dl-user-agent-check"
    success = await asyncio.to_thread(web_browser.open, url, autoraise=False)
    if not success:
        return

    try:
        client, _ = await asyncio.wait_for(loop.sock_accept(server), 10)
    except TimeoutError:
        return

    server.close()
    response_data = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
    response_data += """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>CDL User-Agent</title>
        </head>
        <body>
            <h2>Cyberdrop-DL will use this user-agent:</h2>
    """

    try:
        headers, _ = await get_headers_and_body(client)
        user_agent = headers["user-agent"]
        date_str = f"<h2>Startup time:</h2>{constants.STARTUP_TIME.isoformat()}"
        response_data += f"<p>{user_agent}</p><p>{date_str}</p></body></html>"
        await asyncio.to_thread(client.sendall, response_data.encode("utf-8"))
        return user_agent
    finally:
        client.close()


async def get_headers_and_body(socket: socket.socket) -> tuple[CIMultiDict, bytes]:
    """Reads data from a socket in chunks and extracts the HTTP headers + body."""

    response_data = b""
    body_start = -1
    chunk_size = 512
    loop = asyncio.get_running_loop()

    while True:
        chunk = await loop.sock_recv(socket, chunk_size)
        if not chunk:
            if not response_data:
                raise ConnectionError("No data received")
            break

        response_data += chunk
        body_start = response_data.find(b"\r\n\r\n")
        if body_start != -1:
            break

    if body_start == -1:
        raise ConnectionError("Incomplete headers received")

    headers_bytes, body_bytes = response_data[:body_start], response_data[body_start + 4 :]

    def gen_header_pairs() -> Generator[tuple[str, str]]:
        for line in headers_bytes.decode("utf-8").splitlines():
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            yield name.strip(), value.strip()

    return CIMultiDict(gen_header_pairs()), body_bytes


if __name__ == "__main__":
    print(asyncio.run(get_browser_user_agent()))  # noqa: T201
