"""VIZ (www.viz.com), VIZ Media's English-language Shonen Jump: the chapters it puts up to read on the web.

A Rails site with a reader of its own (`manifest-viz-reader-*.js`):

- A series page, `/shonenjump/chapters/<series>`, names the work in its
  `h2` and credits it in the `span` under it (`Created by ...`,
  `Story by ..., Art by ...`). Every chapter is an `a#ch-<n>` (the chapter
  number times 100, so `112.1` is `ch-11210`), newest first. A chapter
  anyone may read links its page in `href`; a members-only one has
  `javascript:` there and names its page in `onclick`/`data-target-url`
  instead.
- A chapter page, `/shonenjump/<series>-chapter-<n>/chapter/<id>` (the
  listing adds `?action=read`, which opens the reader at once), names the
  work and the chapter in `h3.section_title`, says when it came out in the
  `div.style-italic` under it, and sets the reader's variables in a script:
  `mangaCommonId` (the chapter's id), `pages` (how many) and `isPreview`.
- The reader asks `/manga/get_manga_url?device_id=3&manga_id=<id>&pages=<n>`
  for page `n`, counted from 1, and gets back `{"ok": 1, "data": {"<n>":
  "<signed CloudFront URL>"}}`. A signed URL is good for about two minutes,
  so each page is signed just before it is fetched rather than all at once.
- Members-only chapters open for a signed-in Shonen Jump member. Signing in
  takes the CSRF token `/account/refresh_login_links` sets as `AUTH_TOKEN`,
  sent back as `X-CSRF-Token` with a form POST of `login` and `pass` to
  `/account/try_login`, which answers `{"ok": 1, "user_id": ...}` (or a 401
  with `{"ok": 0}`) and leaves the session cookie behind. Before opening a
  members-only chapter, the site asks `/manga/auth?device_id=3&manga_id=<id>`,
  which says `{"ok": 1, "archive_info": {"ok": 1}}` when the account may
  read it.
- A page is served scrambled. The JPEG is the page cut into a 10 x 15 grid
  of cells with a 10px gutter between them; the outer ring of cells is in
  place, and the 8 x 13 inner cells are shuffled. The EXIF `ImageUniqueID`
  lists, in hex and `:`-separated, where each inner cell goes, and the EXIF
  image size is the page's own.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from PIL import Image

from getjmanga.errors import GetjmangaError, LoginError, NotAnEpisodePageError, UnsupportedUrlError
from getjmanga.extractor import Episode, Extractor, Page, neighbours, ordinal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx2 import Client, Response

_log = logging.getLogger(__name__)

BASE_URL = "https://www.viz.com"
#: Where the reader asks for a page's signed URL.
PAGE_API = f"{BASE_URL}/manga/get_manga_url"
#: Where the site asks whether the account may read a members-only chapter.
AUTH_API = f"{BASE_URL}/manga/auth"
#: What hands out the CSRF token, inside the account links it renders.
LOGIN_LINKS = f"{BASE_URL}/account/refresh_login_links"
#: Where the sign-in form posts.
LOGIN_API = f"{BASE_URL}/account/try_login"
#: Seconds to wait before each new try of a request VIZ turned away with 429 Too Many
#: Requests, unless it says how long in `Retry-After`; after the last, the 429 stands.
#: The page API starts doing so a few hundred pages into a long series.
BACKOFF = (10, 30, 60, 120, 300)
#: What the site's own scripts send with every XHR.
_XHR = {"X-Requested-With": "XMLHttpRequest"}

# A series page: `/shonenjump/chapters/<series>`.
_SERIES_PATH = re.compile(r"^/shonenjump/chapters/(?P<series>[^/]+)/?$")
# A chapter page: `/shonenjump/<series>-chapter-<n>/chapter/<id>`.
_CHAPTER_PATH = re.compile(r"^/shonenjump/(?P<slug>[^/]+)/chapter/(?P<id>\d+)/?$")
# A chapter page's path, wherever the listing puts it: `href`, `onclick` or `data-target-url`.
_CHAPTER_LINK = re.compile(r"/shonenjump/[^/'\"?]+/chapter/\d+")
# A reader variable the chapter page's script sets: `var pages = 15;`.
_READER_VAR = re.compile(r"\bvar\s+(?P<name>\w+)\s*=\s*(?P<value>[^;\n]*);")
# The CSRF token the account links set: `AUTH_TOKEN = "...";`.
_AUTH_TOKEN = re.compile(r"\bAUTH_TOKEN\s*=\s*\"(?P<token>[^\"]+)\"")
# The anchor id of a listed chapter: `ch-<number times 100>`.
_CHAPTER_ANCHOR = re.compile(r"^ch-(?P<order>\d+)$")

#: The grid a page is cut into, and the gutter between its cells in the served image.
COLUMNS, ROWS, GUTTER = 10, 15, 10


@dataclass(frozen=True)
class Chapter:
    """One chapter as the series page lists it."""

    url: str
    #: The chapter number as the site writes it: `137`, `112.1`.
    label: str
    #: Whether anyone may read it, rather than members only.
    free: bool
    #: The anchor's `ch-<n>`, for putting the listing in order.
    order: int


@dataclass(frozen=True)
class Series:
    """What a series page says."""

    title: str
    writer: str
    #: Every listed chapter, oldest first.
    chapters: tuple[Chapter, ...]

    def chapter(self, url: str) -> Chapter | None:
        """The listed chapter `url` names, or None when the listing does not have it."""
        return next((chapter for chapter in self.chapters if chapter.url == url), None)


@dataclass(frozen=True)
class Reader:
    """What a chapter page says."""

    series_title: str
    series_url: str | None
    episode_title: str
    manga_id: str
    pages: int
    preview: bool
    published: date | None
    #: The reader's variables as the page sets them, for `--metadata`.
    variables: dict[str, str]


def chapter_url(path: str) -> str:
    """A chapter's URL, spelled one way: absolute, without `?action=read` or a trailing slash."""
    return urljoin(BASE_URL, path).split("?", 1)[0].rstrip("/")


def parse_series(html: str | bytes) -> Series:
    """Read the title, the credits and every listed chapter off a series page.

    Args:
        html: The page.

    Returns:
        The series, its chapters oldest first.
    """
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h2.weight-bold")
    credit = soup.select_one("div.type-rg > span")
    chapters: dict[str, Chapter] = {}
    for chapter in _listed(soup):
        chapters.setdefault(chapter.url, chapter)
    return Series(
        title=_text(heading),
        writer=_text(credit).removeprefix("Created by "),
        chapters=tuple(sorted(chapters.values(), key=lambda chapter: chapter.order)),
    )


def _listed(soup: BeautifulSoup) -> Iterator[Chapter]:
    for anchor in soup.find_all("a", id=_CHAPTER_ANCHOR):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor.get("href") or "")
        where = " ".join(str(anchor.get(name) or "") for name in ("href", "onclick", "data-target-url"))
        link = _CHAPTER_LINK.search(where)
        order = _CHAPTER_ANCHOR.match(str(anchor.get("id")))
        if link is None or order is None:
            continue
        yield Chapter(
            url=chapter_url(link.group()),
            label=str(anchor.get("name") or ""),
            free=href.startswith("/shonenjump/"),
            order=int(order["order"]),
        )


def _text(tag: object) -> str:
    """A tag's text, its runs of whitespace (no-break spaces among them) made one space each."""
    return " ".join(tag.get_text(" ").split()) if isinstance(tag, Tag) else ""


def parse_reader(html: str | bytes) -> Reader:
    """Read the titles, the date and the reader's variables off a chapter page.

    Args:
        html: The page.

    Returns:
        What the page says; `manga_id` is empty when the page has no reader.
    """
    text = html.decode(errors="replace") if isinstance(html, bytes) else html
    soup = BeautifulSoup(text, "html.parser")
    variables = {match["name"]: match["value"].strip() for match in _READER_VAR.finditer(text)}
    heading = soup.select_one("h3.section_title")
    series_link = heading.select_one("a") if isinstance(heading, Tag) else None
    chapter = heading.select_one("span") if isinstance(heading, Tag) else None
    series_href = str(series_link.get("href") or "") if isinstance(series_link, Tag) else ""
    return Reader(
        series_title=_text(series_link),
        series_url=urljoin(BASE_URL, series_href) if _SERIES_PATH.match(series_href) else None,
        episode_title=_text(chapter),
        manga_id=variables.get("mangaCommonId", "") if variables.get("mangaCommonId", "").isdigit() else "",
        pages=int(variables["pages"]) if variables.get("pages", "").isdigit() else 0,
        preview=variables.get("isPreview") == "true",
        published=_released(soup),
        variables=variables,
    )


def _released(soup: BeautifulSoup) -> date | None:
    """The day the chapter came out, from the first `November  1, 2026` under the heading."""
    for line in soup.select("div.style-italic"):
        try:
            return datetime.strptime(_text(line), "%B %d, %Y").date()  # noqa: DTZ007 (a bare day)
        except ValueError:
            continue
    return None


def unscramble(image: Image.Image) -> Image.Image:
    """Put a served page back together, going by the cell order its EXIF carries.

    Args:
        image: The page as served, its EXIF intact.

    Returns:
        The page; the image as served when its EXIF carries no order.

    Raises:
        GetjmangaError: The EXIF order is not one the grid can take.
    """
    exif = image.getexif()
    key = str(exif.get_ifd(0x8769).get(0xA420) or "")  # Exif IFD, ImageUniqueID
    width, height = exif.get(0x0100), exif.get(0x0101)  # ImageWidth, ImageLength
    if not key or not isinstance(width, int) or not isinstance(height, int):
        return image
    try:
        order = [int(cell, 16) for cell in key.split(":")]
    except ValueError as exc:
        msg = f"the page's cell order is not hex: {key!r}."
        raise GetjmangaError(msg) from exc
    inner = (COLUMNS - 2) * (ROWS - 2)
    if sorted(order) != list(range(inner)):
        msg = f"the page's cell order is not a shuffle of {inner} cells: {key!r}."
        raise GetjmangaError(msg)

    cell_w, cell_h = width // COLUMNS, height // ROWS
    page = Image.new(image.mode, (width, height))

    def move(left: int, top: int, size: tuple[int, int], to: tuple[int, int]) -> None:
        page.paste(image.crop((left, top, left + size[0], top + size[1])), to)

    # The ring of cells in place: the top row, the left column, the bottom row and the right column.
    middle = height - 2 * cell_h
    move(0, 0, (width, cell_h), (0, 0))
    move(0, cell_h + GUTTER, (cell_w, middle), (0, cell_h))
    bottom = (ROWS - 1) * (cell_h + GUTTER)
    move(0, bottom, (width, image.height - bottom), (0, (ROWS - 1) * cell_h))
    right = width - (COLUMNS - 1) * cell_w
    move((COLUMNS - 1) * (cell_w + GUTTER), cell_h + GUTTER, (right, middle), ((COLUMNS - 1) * cell_w, cell_h))
    # The inner cells, each to the slot the order names.
    across = COLUMNS - 2
    for source, target in enumerate(order):
        move(
            (source % across + 1) * (cell_w + GUTTER),
            (source // across + 1) * (cell_h + GUTTER),
            (cell_w, cell_h),
            ((target % across + 1) * cell_w, (target // across + 1) * cell_h),
        )
    return page


class Viz(Extractor):
    """Fetch Shonen Jump chapters from VIZ: the free ones, and a member's."""

    NAME = "viz"
    HOSTS = ("www.viz.com",)
    PUBLISHER = "VIZ Media"
    URL_FORMS = (
        "https://www.viz.com/shonenjump/<series>-chapter-<n>/chapter/<id>",
        "https://www.viz.com/shonenjump/chapters/<series>",
    )

    def __init__(self, session: Client | None = None) -> None:
        """Build an extractor.

        Args:
            session: A session to reuse. A retrying one is made when omitted.
        """
        super().__init__(session)
        #: Series pages read, by URL: `episode()` and `series_urls()` share them.
        self._series: dict[str, Series] = {}
        #: Whether `login()` signed in, so members-only chapters are worth asking about.
        self._signed_in = False

    @classmethod
    def suitable(cls, url: str) -> bool:
        """Report whether `url` is a Shonen Jump chapter or series page.

        Args:
            url: The URL to check.

        Returns:
            True for `/shonenjump/chapters/<series>` and `/shonenjump/<slug>/chapter/<id>`.
        """
        if not super().suitable(url):
            return False
        path = urlparse(url).path
        return bool(_SERIES_PATH.match(path) or _CHAPTER_PATH.match(path))

    @classmethod
    def is_series(cls, url: str) -> bool:
        """Report whether `url` is a series page, `/shonenjump/chapters/<series>`.

        Args:
            url: The URL to check.

        Returns:
            True for a series page.
        """
        return _SERIES_PATH.match(urlparse(url).path) is not None

    def series_urls(self, url: str) -> list[str]:
        """List every chapter of a series, oldest first, members-only ones included.

        Args:
            url: A `/shonenjump/chapters/<series>` URL.

        Returns:
            One chapter URL per listed chapter.

        Raises:
            UnsupportedUrlError: The URL is not a series page.
            NotAnEpisodePageError: The series is not there, or lists no chapter.
        """
        if not self.is_series(url):
            msg = f"{url} is not a series page."
            raise UnsupportedUrlError(msg)
        series = self._read_series(url)
        if not series.chapters:
            msg = f"the series at {url} lists no chapter."
            raise NotAnEpisodePageError(msg)
        return [chapter.url for chapter in series.chapters]

    def episode(self, url: str) -> Episode:
        """Read one chapter: its titles, its pages and the chapters either side.

        Args:
            url: A `/shonenjump/<slug>/chapter/<id>` URL.

        Returns:
            The chapter; without pages when it is for members only or a preview.

        Raises:
            UnsupportedUrlError: The URL is a series page rather than a chapter.
            NotAnEpisodePageError: The chapter is not there, or the page has no reader.
        """
        match = _CHAPTER_PATH.match(urlparse(url).path)
        if match is None:
            msg = f"{url} is not a chapter page (a series page is listed with series_urls())."
            raise UnsupportedUrlError(msg)
        canonical = chapter_url(urlparse(url).path)
        reader = parse_reader(self._page(url).content)
        if not reader.manga_id:
            msg = f"no reader on {url}."
            raise NotAnEpisodePageError(msg)

        series = self._read_series(reader.series_url) if reader.series_url else Series(reader.series_title, "", ())
        listed = series.chapter(canonical)
        urls = [chapter.url for chapter in series.chapters]
        before, after = neighbours(urls, canonical)
        members_only = listed is not None and not listed.free
        # A chapter the listing does not have is tried anyway: the page API says no when it may not be read.
        # A members-only one is asked about only once signed in, and the way the site asks: `/manga/auth` first.
        readable = (
            not reader.preview
            and (not members_only or (self._signed_in and self._authorised(reader.manga_id, url)))
            and self._readable(reader.manga_id, url)
        )

        return Episode(
            url=url,
            series_title=reader.series_title or series.title,
            episode_title=reader.episode_title,
            pages=tuple(Page(url=self._page_api(reader.manga_id, n)) for n in range(1, reader.pages + 1))
            if readable
            else (),
            next_url=after,
            prev_url=before,
            metadata={
                "manga_id": reader.manga_id,
                "series_id": reader.variables.get("mangaSeriesCommonId", ""),
                "chapter_number": reader.variables.get("chapterNumber", ""),
                "pages": reader.pages,
                "preview": reader.preview,
                "members_only": members_only,
                "series_url": reader.series_url,
            },
            writer=series.writer,
            publisher=self.PUBLISHER,
            published=reader.published,
            number=ordinal(urls, canonical),
        )

    def image(self, page: Page, episode: Episode) -> Image.Image:
        """Sign one page, fetch it and put its cells back in order.

        Args:
            page: The page, its URL the page API's for it.
            episode: The chapter the page belongs to.

        Returns:
            The page image.
        """
        signed = self._signed(page.url, episode.url)
        if signed is None:
            msg = f"VIZ would not sign {page.url}."
            raise GetjmangaError(msg)
        return unscramble(self._fetch_image(signed, headers={**self.HEADERS, "Referer": episode.url}))

    def login(self, url: str, username: str, password: str) -> None:  # noqa: ARG002 (one sign-in endpoint)
        """Sign in, so the members-only chapters a Shonen Jump membership opens become readable.

        Args:
            url: Ignored; VIZ has one sign-in endpoint.
            username: The account's email address or username.
            password: The account's password.

        Raises:
            LoginError: VIZ handed out no CSRF token, or refused the credentials.
        """
        links = self._get(LOGIN_LINKS, headers={**self.HEADERS, **_XHR})
        token = _AUTH_TOKEN.search(links.text)
        if token is None:
            msg = f"{LOGIN_LINKS} handed out no CSRF token."
            raise LoginError(msg)
        res = self._session.post(
            LOGIN_API,
            data={"login": username, "pass": password, "rem_user": "on", "uid": "0"},
            headers={
                **self.HEADERS,
                **_XHR,
                "X-CSRF-Token": token["token"],
                "Accept": "application/json",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/shonenjump",
            },
            timeout=self.TIMEOUT,
        )
        try:
            answer = res.json()
        except ValueError:
            answer = None
        if not res.is_success or not isinstance(answer, dict) or answer.get("ok") != 1:
            msg = f"VIZ refused the login for {username} (HTTP {res.status_code})."
            raise LoginError(msg)
        self._signed_in = True

    def _authorised(self, manga_id: str, referer: str) -> bool:
        """Whether `/manga/auth` lets the signed-in account read a members-only chapter."""
        res = self._api(AUTH_API, referer, params={"device_id": 3, "manga_id": manga_id})
        try:
            body = res.json()
        except ValueError:
            return False
        archive = body.get("archive_info") if isinstance(body, dict) else None
        return isinstance(archive, dict) and body.get("ok") == 1 and archive.get("ok") == 1

    def _readable(self, manga_id: str, referer: str) -> bool:
        """Whether the page API signs the chapter's first page for this session."""
        return self._signed(self._page_api(manga_id, 1), referer) is not None

    def _signed(self, api_url: str, referer: str) -> str | None:
        """The signed URL the page API hands back for one page, or None when it says no."""
        res = self._api(api_url, referer)
        try:
            body = res.json()
        except ValueError:
            return None
        if not isinstance(body, dict) or body.get("ok") != 1 or not isinstance(body.get("data"), dict):
            return None
        page = api_url.rsplit("=", 1)[-1]
        signed = body["data"].get(page)
        return signed if isinstance(signed, str) and signed.startswith("https://") else None

    def _api(self, url: str, referer: str, params: dict[str, str | int] | None = None) -> Response:
        """GET one of the reader's API endpoints, waiting out its 429s."""
        res = self._patiently(url, {**self.HEADERS, **_XHR, "Referer": referer}, params)
        res.raise_for_status()
        return res

    def _patiently(self, url: str, headers: dict[str, str], params: dict[str, str | int] | None = None) -> Response:
        """GET `url`, waiting out a 429 the way `BACKOFF` says; the last answer, whatever its status."""
        for wait in (*BACKOFF, None):
            res = self._session.get(url, headers=headers, params=params, timeout=self.TIMEOUT)
            if res.status_code != HTTPStatus.TOO_MANY_REQUESTS or wait is None:
                break
            asked = res.headers.get("Retry-After", "")
            delay = int(asked) if asked.isdigit() else wait
            _log.info("viz: too many requests; waiting %d seconds.", delay)
            time.sleep(delay)
        return res

    @staticmethod
    def _page_api(manga_id: str, page: int) -> str:
        return f"{PAGE_API}?{urlencode({'device_id': 3, 'manga_id': manga_id, 'pages': page})}"

    def _read_series(self, url: str) -> Series:
        """Fetch a series page once and keep what it says."""
        key = url.rstrip("/")
        if key not in self._series:
            self._series[key] = parse_series(self._page(url).content)
        return self._series[key]

    def _page(self, url: str) -> Response:
        res = self._patiently(url, self.HEADERS)
        if res.status_code == HTTPStatus.NOT_FOUND:
            msg = f"{url} is not there (404)."
            raise NotAnEpisodePageError(msg)
        res.raise_for_status()
        return res
