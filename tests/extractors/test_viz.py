from __future__ import annotations

import json
from http import HTTPStatus
from io import BytesIO

import pytest
from httpx2 import HTTPStatusError
from PIL import Image

from getjmanga.downloader import Downloader
from getjmanga.errors import GetjmangaError, LoginError, NotAnEpisodePageError, UnsupportedUrlError
from getjmanga.extractors.viz import BACKOFF, COLUMNS, GUTTER, ROWS, Viz, unscramble

SERIES_URL = "https://www.viz.com/shonenjump/chapters/kagurabachi"
FIRST_URL = "https://www.viz.com/shonenjump/kagurabachi-chapter-1/chapter/40223"
SPECIAL_URL = "https://www.viz.com/shonenjump/kagurabachi-chapter-1-1/chapter/40250"
EPISODE_URL = "https://www.viz.com/shonenjump/kagurabachi-chapter-2/chapter/40275"
LOCKED_URL = "https://www.viz.com/shonenjump/kagurabachi-chapter-3/chapter/40288"
LATEST_URL = "https://www.viz.com/shonenjump/kagurabachi-chapter-4/chapter/52108"
PAGE_API = "https://www.viz.com/manga/get_manga_url?device_id=3&manga_id=40275&pages="
LOCKED_PAGE_API = "https://www.viz.com/manga/get_manga_url?device_id=3&manga_id=40288&pages="
# What `/account/refresh_login_links` renders, CSRF token and all.
LOGIN_LINKS_HTML = """<div id="o_account-links-content" logged_in="false"></div>
<script>
  AUTH_TOKEN = "csrf-token==";
  $.ajaxSetup({ headers: { 'X-CSRF-Token': AUTH_TOKEN } });
  user_id           = 0;
  is_sj_subscriber  = false;
</script>"""
CDN = "https://d2vs6ffylckc3p.cloudfront.net/manga/new/chapter/kagurabachi2/sc"


def free_link(number: str, path: str) -> str:
    """A chapter anyone may read, the way the listing writes it: the page in `href`."""
    order = round(float(number) * 100)
    return f'<a id="ch-{order}" name="{number}" href="{path}?action=read" data-target-url="{path}?action=read">'


def locked_link(number: str, path: str) -> str:
    """A members-only chapter: `javascript:` in `href`, the page only in `onclick`."""
    order = round(float(number) * 100)
    return (
        f'<a id="ch-{order}" name="{number}" href="javascript:void(\'join to read\');" '
        f"onclick=\"Modals.context={{targetUrl:'{path}?action=read',targetTitle:'Kagurabachi'}};\" "
        f"data-target-url=\"javascript:tryReadChapter(1,'{path}?action=read');\">"
    )


def series_html(*links: str) -> str:
    rows = "".join(f"<tr class='o_chapter'><td>{link}Ch.</a></td></tr>" for link in links)
    return f"""<html><head><title>VIZ | Read Kagurabachi Manga</title></head><body>
<div><h2 class="type-lg line-solid weight-bold style-caps mar-b-rg">Kagurabachi</h2>
<div class="line-solid type-md">Chihiro's cursed past sends him down a quest of bloody vengeance!</div></div>
<div class="type-rg"><span class="disp-bl--bm mar-b-md">Created by Takeru Hokazono</span></div>
<table>{rows}</table></body></html>"""


# Newest first, as the site lists them, the latest one repeated above the archive.
SERIES_HTML = series_html(
    free_link("4", "/shonenjump/kagurabachi-chapter-4/chapter/52108"),
    free_link("4", "/shonenjump/kagurabachi-chapter-4/chapter/52108"),
    locked_link("3", "/shonenjump/kagurabachi-chapter-3/chapter/40288"),
    free_link("2", "/shonenjump/kagurabachi-chapter-2/chapter/40275"),
    locked_link("1.1", "/shonenjump/kagurabachi-chapter-1-1/chapter/40250"),
    free_link("1", "/shonenjump/kagurabachi-chapter-1/chapter/40223"),
)


def chapter_html(manga_id: str = "40275", number: str = "2", *, pages: int = 3, preview: bool = False) -> str:
    return f"""<html><head><title>VIZ: Read Kagurabachi, Chapter {number} Manga</title></head><body>
<h3 class="section_title line-solid type-md">
  <a class="color-black" href="/shonenjump/chapters/kagurabachi">Kagurabachi</a>,
  <span style="white-space:nowrap;">Chapter&nbsp;{number}</span>
</h3>
<div class="color-off-black style-italic type-sm">September 24, 2023</div>
<script>
  isPreview    = {str(preview).lower()};
  pages        = {pages};
</script>
<script type="text/javascript">
  var pages        = {pages};
  var isPreview    = {str(preview).lower()};
  var mangaSeriesCommonId = 924;
  var mangaCommonId       = {manga_id};
  var chapterNumber       = {number}.0;
  var seriesTitle         = "Kagurabachi";
</script>
<div class="flex-width-2 type-bs style-italic"><table><tr><td>October  4, 2026</td></tr></table></div>
</body></html>"""


def signed(page: int) -> dict:
    return {"ok": 1, "data": {str(page): f"{CDN}/{page}.jpg?Expires=1794006244&Signature=x"}, "metadata": ""}


@pytest.fixture
def client(fake_session, fake_response):
    def build(routes=None):
        merged = dict(routes or {})
        merged.setdefault("chapters/kagurabachi", fake_response(text=SERIES_HTML))
        merged.setdefault("chapter/40275", fake_response(text=chapter_html()))
        merged.setdefault("chapter/40288", fake_response(text=chapter_html("40288", "3")))
        for page in (1, 2, 3):
            merged.setdefault(f"{PAGE_API}{page}", fake_response(payload=signed(page), content_type="application/json"))
            merged.setdefault(
                f"{LOCKED_PAGE_API}{page}", fake_response(payload=signed(page), content_type="application/json")
            )
        merged.setdefault("refresh_login_links", fake_response(text=LOGIN_LINKS_HTML))
        merged.setdefault(
            "try_login", fake_response(payload={"ok": 1, "user_id": 123}, content_type="application/json")
        )
        merged.setdefault(
            "manga/auth", fake_response(payload={"ok": 1, "archive_info": {"ok": 1}}, content_type="application/json")
        )
        session = fake_session(merged)
        return Viz(session), session

    return build


# --- URLs ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        SERIES_URL,
        f"{SERIES_URL}/",
        EPISODE_URL,
        f"{EPISODE_URL}?action=read",
        SPECIAL_URL,
    ],
)
def test_suitable_accepts_series_and_chapter_pages(url):
    assert Viz.suitable(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.viz.com/shonenjump/chapters/kagurabachi",
        "https://example.com/shonenjump/chapters/kagurabachi",
        "https://www.viz.com/shonenjump",
        "https://www.viz.com/shonenjump/chapters",
        "https://www.viz.com/vizmanga/chapters/kagurabachi",
        "https://www.viz.com/read/manga/kagurabachi-volume-1/product/7975",
        "https://www.viz.com/manga/get_manga_url?device_id=3&manga_id=40275&pages=1",
    ],
)
def test_suitable_rejects_other_pages(url):
    assert not Viz.suitable(url)


def test_is_series_tells_a_series_page_from_a_chapter():
    assert Viz.is_series(SERIES_URL)
    assert Viz.is_series(f"{SERIES_URL}/")
    assert not Viz.is_series(EPISODE_URL)


# --- episodes -----------------------------------------------------------------------------------


def test_episode_reads_the_titles_the_pages_and_the_neighbours(client):
    viz, session = client()
    episode = viz.episode(f"{EPISODE_URL}?action=read")

    assert (episode.series_title, episode.episode_title) == ("Kagurabachi", "Chapter 2")
    assert (episode.writer, episode.publisher) == ("Takeru Hokazono", "VIZ Media")
    assert str(episode.published) == "2023-09-24"
    assert episode.number == 3
    assert (episode.prev_url, episode.next_url) == (SPECIAL_URL, LOCKED_URL)
    assert [page.url for page in episode.pages] == [f"{PAGE_API}{page}" for page in (1, 2, 3)]
    assert episode.metadata == {
        "manga_id": "40275",
        "series_id": "924",
        "chapter_number": "2.0",
        "pages": 3,
        "preview": False,
        "members_only": False,
        "series_url": SERIES_URL,
    }
    json.dumps(episode.metadata)
    assert session.calls == [f"{EPISODE_URL}?action=read", SERIES_URL, f"{PAGE_API}1"]
    assert session.headers_seen[-1]["Referer"] == f"{EPISODE_URL}?action=read"


def test_members_only_episode_is_not_readable_and_not_asked_for(client):
    viz, session = client()
    episode = viz.episode(LOCKED_URL)

    assert not episode.readable
    assert episode.episode_title == "Chapter 3"
    assert episode.metadata["members_only"] is True
    assert (episode.prev_url, episode.next_url) == (EPISODE_URL, LATEST_URL)
    assert not any("/manga/" in url for url in session.calls)  # neither /manga/auth nor the page API


def test_episode_the_page_api_refuses_is_not_readable(client, fake_response):
    viz, _ = client(
        {f"{PAGE_API}1": fake_response(payload={"ok": 0, "error": "nope"}, content_type="application/json")}
    )
    episode = viz.episode(EPISODE_URL)

    assert not episode.readable
    assert episode.next_url == LOCKED_URL


def test_preview_episode_is_not_readable(client, fake_response):
    viz, session = client({"chapter/40275": fake_response(text=chapter_html(preview=True))})
    episode = viz.episode(EPISODE_URL)

    assert not episode.readable
    assert episode.metadata["preview"] is True
    assert not any("get_manga_url" in url for url in session.calls)


def test_episode_answered_404_is_not_an_episode(client, fake_response):
    viz, _ = client({"chapter/40275": fake_response(text="<html></html>", status_code=HTTPStatus.NOT_FOUND)})
    with pytest.raises(NotAnEpisodePageError, match="404"):
        viz.episode(EPISODE_URL)


def test_episode_page_without_a_reader_is_not_an_episode(client, fake_response):
    viz, _ = client({"chapter/40275": fake_response(text="<html><body><p>meh</p></body></html>")})
    with pytest.raises(NotAnEpisodePageError, match="no reader"):
        viz.episode(EPISODE_URL)


def test_episode_refuses_a_series_url(client):
    viz, _ = client()
    with pytest.raises(UnsupportedUrlError, match="series_urls"):
        viz.episode(SERIES_URL)


# --- series -------------------------------------------------------------------------------------


def test_series_urls_lists_every_chapter_oldest_first(client):
    viz, session = client()
    urls = viz.series_urls(SERIES_URL)

    assert urls == [FIRST_URL, SPECIAL_URL, EPISODE_URL, LOCKED_URL, LATEST_URL]
    assert all(Viz.suitable(url) and not Viz.is_series(url) for url in urls)
    assert session.calls == [SERIES_URL]


def test_series_urls_raises_on_an_empty_listing(client, fake_response):
    viz, _ = client({"chapters/kagurabachi": fake_response(text=series_html())})
    with pytest.raises(NotAnEpisodePageError, match="lists no chapter"):
        viz.series_urls(SERIES_URL)


def test_series_urls_raises_on_a_missing_series(client, fake_response):
    viz, _ = client({"chapters/kagurabachi": fake_response(text="", status_code=HTTPStatus.NOT_FOUND)})
    with pytest.raises(NotAnEpisodePageError, match="404"):
        viz.series_urls(SERIES_URL)


def test_series_urls_refuses_a_chapter_url(client):
    viz, _ = client()
    with pytest.raises(UnsupportedUrlError):
        viz.series_urls(EPISODE_URL)


# --- signing in --------------------------------------------------------------------------------


def test_login_posts_the_credentials_with_the_csrf_token(client):
    viz, session = client()
    viz.login(EPISODE_URL, "someone@example.com", "hunter2")

    assert session.calls == ["https://www.viz.com/account/refresh_login_links"]
    url, body = session.posts[0]
    assert url == "https://www.viz.com/account/try_login"
    assert body == {"login": "someone@example.com", "pass": "hunter2", "rem_user": "on", "uid": "0"}
    assert session.post_headers[0]["X-CSRF-Token"] == "csrf-token=="
    assert session.post_headers[0]["X-Requested-With"] == "XMLHttpRequest"


def test_login_raises_when_viz_refuses_the_credentials(client, fake_response):
    refused = fake_response(payload={"ok": 0}, status_code=HTTPStatus.UNAUTHORIZED, content_type="application/json")
    viz, _ = client({"try_login": refused})
    with pytest.raises(LoginError, match="HTTP 401"):
        viz.login(EPISODE_URL, "someone@example.com", "wrong")


def test_login_raises_without_a_csrf_token(client, fake_response):
    viz, session = client({"refresh_login_links": fake_response(text="<div>maintenance</div>")})
    with pytest.raises(LoginError, match="CSRF"):
        viz.login(EPISODE_URL, "someone@example.com", "hunter2")
    assert session.posts == []


def test_members_only_episode_is_readable_once_a_member_signs_in(client):
    viz, session = client()
    viz.login(EPISODE_URL, "someone@example.com", "hunter2")
    episode = viz.episode(LOCKED_URL)

    assert [page.url for page in episode.pages] == [f"{LOCKED_PAGE_API}{page}" for page in (1, 2, 3)]
    assert episode.metadata["members_only"] is True
    assert session.calls[-2:] == ["https://www.viz.com/manga/auth", f"{LOCKED_PAGE_API}1"]
    assert session.params_seen[-2] == {"device_id": 3, "manga_id": "40288"}
    assert session.headers_seen[-2]["Referer"] == LOCKED_URL


def test_members_only_episode_stays_locked_for_an_account_without_membership(client, fake_response):
    no_membership = fake_response(
        payload={"ok": 1, "archive_info": {"ok": 0, "err": {"code": 4}}}, content_type="application/json"
    )
    viz, session = client({"manga/auth": no_membership})
    viz.login(EPISODE_URL, "someone@example.com", "hunter2")
    episode = viz.episode(LOCKED_URL)

    assert not episode.readable
    assert episode.next_url == LATEST_URL
    assert not any("get_manga_url" in url for url in session.calls)


# --- unscrambling -------------------------------------------------------------------------------

WIDTH, HEIGHT = 10 * COLUMNS + 3, 10 * ROWS + 7  # the right column and the bottom row take the remainder


def noise(width: int, height: int) -> Image.Image:
    """An image no two cells of which look alike, so a misplaced cell shows."""
    return Image.frombytes(
        "RGB", (width, height), bytes((i * 2654435761 >> 11) & 0xFF for i in range(width * height * 3))
    )


def scramble(page: Image.Image, order: list[int]) -> Image.Image:
    """Serve a page the way VIZ does: the ring as whole strips, the inner cells apart by the gutter and shuffled."""
    width, height = page.size
    cell_w, cell_h = width // COLUMNS, height // ROWS
    served = Image.new("RGB", (width + (COLUMNS - 1) * GUTTER, height + (ROWS - 1) * GUTTER))
    served.paste(page.crop((0, 0, width, cell_h)), (0, 0))
    served.paste(page.crop((0, cell_h, cell_w, height - cell_h)), (0, cell_h + GUTTER))
    served.paste(page.crop((0, (ROWS - 1) * cell_h, width, height)), (0, (ROWS - 1) * (cell_h + GUTTER)))
    right = (COLUMNS - 1) * cell_w
    served.paste(
        page.crop((right, cell_h, width, height - cell_h)), ((COLUMNS - 1) * (cell_w + GUTTER), cell_h + GUTTER)
    )
    across = COLUMNS - 2
    for served_cell, page_cell in enumerate(order):
        left, top = (page_cell % across + 1) * cell_w, (page_cell // across + 1) * cell_h
        served.paste(
            page.crop((left, top, left + cell_w, top + cell_h)),
            ((served_cell % across + 1) * (cell_w + GUTTER), (served_cell // across + 1) * (cell_h + GUTTER)),
        )
    return served


def served_png(page: Image.Image, key: str | None) -> bytes:
    exif = Image.Exif()
    if key is not None:
        exif[0x0100], exif[0x0101] = page.size
        exif[0x8769] = {0xA420: key}
    order = [int(cell, 16) for cell in key.split(":")] if key else list(range((COLUMNS - 2) * (ROWS - 2)))
    buffer = BytesIO()
    scramble(page, order).save(buffer, "PNG", exif=exif.tobytes())
    return buffer.getvalue()


def shuffled_key(stride: int = 37) -> str:
    """A cell order as the EXIF writes it; a stride prime to the cell count makes it a shuffle."""
    cells = (COLUMNS - 2) * (ROWS - 2)
    order = [cell * stride % cells for cell in range(cells)]
    return ":".join(f"{cell:02x}" for cell in order)


def test_unscramble_restores_the_page():
    page = noise(WIDTH, HEIGHT)
    served = Image.open(BytesIO(served_png(page, shuffled_key())))

    assert unscramble(served).tobytes() == page.tobytes()


def test_unscramble_hands_back_a_page_without_an_order_as_served():
    served = Image.open(BytesIO(served_png(noise(WIDTH, HEIGHT), None)))
    assert unscramble(served) is served


def test_unscramble_rejects_an_order_that_is_no_shuffle():
    served = Image.open(BytesIO(served_png(noise(WIDTH, HEIGHT), shuffled_key())))
    served.getexif().get_ifd(0x8769)[0xA420] = "00:01:01"
    with pytest.raises(GetjmangaError, match="not a shuffle"):
        unscramble(served)


# --- downloading --------------------------------------------------------------------------------


def test_download_signs_each_page_and_writes_it_unscrambled(client, fake_response, tmp_path):
    page = noise(WIDTH, HEIGHT)
    viz, session = client({f"{CDN}/": fake_response(served_png(page, shuffled_key()), content_type="image/jpeg")})

    result = Downloader(viz, tmp_path, fmt="png", save_metadata=True).download(EPISODE_URL)

    assert result.status == "saved"
    assert result.save_dir == tmp_path / "www.viz.com" / "Kagurabachi" / "Chapter 2"
    with Image.open(result.save_dir / "0.png") as saved:
        assert saved.convert("RGB").tobytes() == page.tobytes()
    assert session.calls[-6:] == [
        f"{PAGE_API}1",
        f"{CDN}/1.jpg?Expires=1794006244&Signature=x",
        f"{PAGE_API}2",
        f"{CDN}/2.jpg?Expires=1794006244&Signature=x",
        f"{PAGE_API}3",
        f"{CDN}/3.jpg?Expires=1794006244&Signature=x",
    ]
    assert session.headers_seen[-1]["Referer"] == EPISODE_URL
    metadata = json.loads((result.save_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["metadata"]["manga_id"] == "40275"


def test_download_fails_on_a_page_the_api_will_not_sign(client, fake_response):
    viz, _ = client()
    episode = viz.episode(EPISODE_URL)
    viz.session.routes[f"{PAGE_API}2"] = fake_response(payload={"ok": 0}, content_type="application/json")

    with pytest.raises(GetjmangaError, match="would not sign"):
        viz.image(episode.pages[1], episode)


def test_a_page_the_api_turns_away_for_too_many_requests_is_tried_again(client, fake_response, monkeypatch):
    waits = []
    monkeypatch.setattr("getjmanga.extractors.viz.time.sleep", waits.append)
    viz, session = client()
    episode = viz.episode(EPISODE_URL)
    session.routes[f"{PAGE_API}2"] = [
        fake_response(status_code=HTTPStatus.TOO_MANY_REQUESTS),
        fake_response(status_code=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "3"}),
        fake_response(payload=signed(2), content_type="application/json"),
    ]

    assert viz._signed(episode.pages[1].url, EPISODE_URL) == f"{CDN}/2.jpg?Expires=1794006244&Signature=x"
    # The first wait is the backoff's own; the second, what the server asked for.
    assert waits == [10, 3]


def test_a_page_the_api_keeps_turning_away_fails_in_the_end(client, fake_response, monkeypatch):
    waits = []
    monkeypatch.setattr("getjmanga.extractors.viz.time.sleep", waits.append)
    viz, session = client()
    episode = viz.episode(EPISODE_URL)
    session.routes[f"{PAGE_API}2"] = fake_response(status_code=HTTPStatus.TOO_MANY_REQUESTS)

    with pytest.raises(HTTPStatusError):
        viz.image(episode.pages[1], episode)
    assert waits == list(BACKOFF)


def test_a_chapter_page_turned_away_for_too_many_requests_is_tried_again(client, fake_response, monkeypatch):
    waits = []
    monkeypatch.setattr("getjmanga.extractors.viz.time.sleep", waits.append)
    viz, session = client()
    session.routes["chapter/40275"] = [
        fake_response(status_code=HTTPStatus.TOO_MANY_REQUESTS),
        session.routes["chapter/40275"],
    ]

    assert len(viz.episode(EPISODE_URL).pages) == 3
    assert waits == [BACKOFF[0]]


# --- the real site --------------------------------------------------------------------


# One chapter per known host, free to read without an account.
TEST_URLS: dict[str, str] = {
    "www.viz.com": "https://www.viz.com/shonenjump/kagurabachi-chapter-1/chapter/40223",
}


@pytest.mark.network
@pytest.mark.parametrize("host", TEST_URLS)
def test_site_download(tmp_path, host):
    result = Downloader(Viz(), tmp_path, only_first=True).download(TEST_URLS[host])
    assert result.status == "saved"
    assert result.episode.series_title == "Kagurabachi"
    assert result.episode.next_url == "https://www.viz.com/shonenjump/kagurabachi-chapter-2/chapter/40275"
    assert (result.save_dir / "0.jpg").stat().st_size > 0


@pytest.mark.network
def test_series_page_lists_chapters():
    urls = Viz().series_urls("https://www.viz.com/shonenjump/chapters/kagurabachi")
    assert urls[0] == "https://www.viz.com/shonenjump/kagurabachi-chapter-1/chapter/40223"
    assert len(urls) >= 137
    assert all(Viz.suitable(url) for url in urls)
