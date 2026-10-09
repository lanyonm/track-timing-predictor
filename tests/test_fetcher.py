"""Tests for app/fetcher.py against an httpx.MockTransport."""

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.fetcher import fetch_initial_layout, fetch_page_html, fetch_refresh

BASE = "https://tracktiming.live"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))


def _run(coro_fn, handler):
    async def go():
        async with _client(handler) as client:
            return await coro_fn(client)

    return asyncio.run(go())


class TestJaxonRequests:
    @pytest.mark.parametrize(
        ("fetch", "jxnfun"),
        [(fetch_initial_layout, "getInitialPageLayout"), (fetch_refresh, "refreshPage")],
    )
    def test_posts_payload_and_headers(self, fetch, jxnfun):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"jxnobj": []})

        assert _run(lambda c: fetch(c, 26008), handler) == {"jxnobj": []}
        (req,) = seen
        assert req.method == "POST"
        assert str(req.url) == f"{BASE}/eventpage.php?EventId=26008"
        assert req.headers["Referer"] == f"{BASE}/eventpage.php?EventId=26008"
        assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert req.headers["X-Requested-With"] == "XMLHttpRequest"
        form = parse_qs(req.content.decode())
        assert form["jxnfun"] == [jxnfun]
        assert form["jxnr"] == ["1"]

    def test_refresh_sends_session_ids_and_group(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        _run(lambda c: fetch_refresh(c, 1), handler)
        args = parse_qs(seen[0].content.decode())["jxnargs[]"]
        assert json.loads(args[0]) == ["1", "2", "3"]
        assert args[1] == "All"

    @pytest.mark.parametrize("fetch", [fetch_initial_layout, fetch_refresh])
    @pytest.mark.parametrize("status", [404, 500])
    def test_raises_on_error_status(self, fetch, status):
        with pytest.raises(httpx.HTTPStatusError):
            _run(lambda c: fetch(c, 26008), lambda _req: httpx.Response(status))


class TestFetchPageHtml:
    def test_gets_relative_path(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text="<html>ok</html>")

        assert _run(lambda c: fetch_page_html(c, "results/E26008/W1.htm"), handler) == "<html>ok</html>"
        assert seen[0].method == "GET"
        assert str(seen[0].url) == f"{BASE}/results/E26008/W1.htm"

    @pytest.mark.parametrize("status", [403, 503])
    def test_raises_on_error_status(self, status):
        with pytest.raises(httpx.HTTPStatusError):
            _run(lambda c: fetch_page_html(c, "results/x.htm"), lambda _req: httpx.Response(status))
