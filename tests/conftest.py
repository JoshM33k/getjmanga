"""Fakes every test file shares: a canned `httpx2.Client` and its responses."""

from __future__ import annotations

import os
from contextlib import nullcontext
from http import HTTPStatus
from typing import cast

import pytest
from httpx2 import Client, HTTPStatusError, Request, Response


class FakeResponse:
    """What a route hands back. `payload` is what `.json()` returns."""

    def __init__(
        self,
        content=b"",
        *,
        text=None,
        payload=None,
        status_code=HTTPStatus.OK,
        url=None,
        content_type="text/html; charset=utf-8",
        headers=None,
    ):
        self.content = content if text is None else text.encode()
        self.text = text if text is not None else content.decode(errors="replace")
        self._payload = payload
        self.status_code = int(status_code)
        self.is_success = self.status_code < HTTPStatus.BAD_REQUEST
        # None means "wherever it was asked for"; a value stands for a redirect.
        self.url = url
        self.headers = {"content-type": content_type, **(headers or {})}

    def raise_for_status(self):
        if not self.is_success:
            request = Request("GET", self.url or "https://fake.invalid/")
            response = cast("Response", self)  # a fake stands in for the real response
            raise HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)
        return self

    def json(self):
        return self._payload


class FakeSession(Client):
    """Answers by substring match on the requested URL, first route wins.

    A route may hold a list of responses, handed out in order; the last one
    is then repeated.
    """

    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.calls = []
        self.heads = []
        self.params_seen = []
        self.headers_seen = []
        self.posts = []
        self.post_headers = []
        self.puts = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        self.params_seen.append(kwargs.get("params"))
        self.headers_seen.append(kwargs.get("headers") or {})
        return self._route(url)

    def head(self, url, **kwargs):
        """Answer a HEAD like a GET, noted in `heads` rather than `calls`.

        An unrouted URL is a 404: a HEAD only ever asks for an optional header,
        so a script need not route the image just to read the episode.
        """
        self.heads.append((url, kwargs.get("headers") or {}))
        try:
            return self._route(url)
        except AssertionError:
            return FakeResponse(status_code=HTTPStatus.NOT_FOUND, url=url)

    def post(self, url, data=None, json=None, content=None, **_kwargs):
        body = data if data is not None else json
        self.posts.append((url, body if body is not None else content))
        self.post_headers.append(_kwargs.get("headers") or {})
        return self._route(url)

    def put(self, url, data=None, **kwargs):
        self.puts.append((url, kwargs.get("params")))
        self.headers_seen.append(kwargs.get("headers") or {})
        return self._route(url)

    def stream(self, method, url, **kwargs):
        assert method == "GET"
        return nullcontext(self.get(url, **kwargs))

    def _route(self, url):
        for needle, scripted in self.routes.items():
            if needle in url:
                response = scripted
                if isinstance(scripted, list):
                    response = scripted.pop(0) if len(scripted) > 1 else scripted[0]
                if response.url is None:
                    response.url = url
                return response
        msg = f"no route for {url}"
        raise AssertionError(msg)


def pytest_collection_modifyitems(items):
    """Skip the `geoblocked` network tests on GitHub's runners.

    Some sites refuse whole networks -- a 403, a 412, or a stand-in page with
    no viewer in it -- and GitHub's are among them. That is the site's call
    about where it serves from, not a bug in the client, so those tests run
    only from a network the site answers.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    skip = pytest.mark.skip(reason="the site refuses requests from GitHub's runners")
    for item in items:
        if item.get_closest_marker("geoblocked"):
            item.add_marker(skip)


@pytest.fixture
def fake_response():
    return FakeResponse


@pytest.fixture
def fake_session():
    return FakeSession


@pytest.fixture
def uploaded():
    """Headers of a page image uploaded on 2025-08-21 in Japan, for a site dated by `_dated_by_upload`."""
    return {"last-modified": "Thu, 21 Aug 2025 08:16:41 GMT"}


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Keep the tests away from the user's own `~/.config/getjmanga/config.toml`."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg" / "getjmanga" / "config.toml"
