from __future__ import annotations

from datetime import date
from http import HTTPStatus
from io import BytesIO

import pytest
from httpx2 import HTTPStatusError
from PIL import Image

from getjmanga.downloader import Downloader
from getjmanga.errors import LoginError, NotAnEpisodePageError, UnsupportedUrlError
from getjmanga.extractors.firecross import UNIT, FireCross, descramble

ORIGIN = "https://firecross.jp"
EPISODE_URL = f"{ORIGIN}/reader/18592"
NEXT_URL = f"{ORIGIN}/reader/18665"
LOCKED_URL = f"{ORIGIN}/reader/18810"
SERIES_URL = f"{ORIGIN}/ebook/series/596"
CGI = f"{ORIGIN}/celsys/diazepam_hybrid.php"
PARAM = "1T6XoHEliHqVjZKvm/obiMljUv7qhn0Dv/VWs8eky6Cd0PK3ogjXKXEFsQm7bnCC=="
TOKEN_URL = f"{ORIGIN}/reader/18592?trial=0&token=4691e0f4-c933-4c41-bc55-45666a75dc95&vertical=0"
TABLE = [15, 6, 13, 2, 12, 0, 4, 1, 5, 14, 3, 11, 7, 8, 10, 9]


def colophon_page(*, episode="第1話", series="コーヴァ -KOHVA-", next_area=""):
    """The colophon page as the site writes it, with the given next-episode area."""
    return f"""
<html><head><title>奥付 - {episode} - {series} | ファイアCROSS</title></head><body id="reader_colophon">
<main class="main">
  <section class="colophonSection bg-gray"><div class="colophonContent">
    <p class="colophonContent__seriesTitle _pc">{series}</p>
    <div class="colophonContent__body">
      <p class="fw-bold _pc">{episode}</p>
      <p style="text-align: center">面白かったら、<br class="_sp">いいね＆コメントで応援してね</p>
    </div>
    <div class="colophonNextArea">
      {next_area}
      <a class="colophonBtn__home" href="{SERIES_URL}"><span>作品ホーム</span></a>
    </div>
  </div></section>
</main></body></html>
"""


FREE_NEXT = f"""
<form data-api="reader" >
  <input type="hidden" name="_token" value="v5jSHZxrknD63mklQLdwZr6SkKcE5sYHx7heyTgo">
  <input type="hidden" name="ebook_id" value="18665">
  <input type="hidden" name="callback" value="{SERIES_URL}">
  <button class="colophonBtn__next"><div class="colophonBtn__nextTitle">
    <p class="fw-bold">次の話を読む</p><p>第2話</p></div></button>
</form>
"""

PAID_NEXT = f"""
<button class="colophonBtn__next" data-modal-source="{ORIGIN}/shop/rental/18963" js-modal>
  <div class="colophonBtn__nextTitle"><p class="fw-bold">次の話を読む</p><p>第4話</p></div>
</button>
"""

LAST_NEXT = """
<div class="colophonContent__complete">
  <p class="fw-bold">最新話まで読んでいただき、ありがとうございます</p><p>次回の更新をお楽しみに！</p>
</div>
"""

READER_HTML = f"""
<html><head><title>第1話 - コーヴァ -KOHVA- | ファイアCROSS</title>
<script src="https://firecross.jp/celsys/js/csr-web-core.js?t=1772002650"></script></head>
<body id="reader_exec" class="no-scroll fix-layout" data-vertical="0">
<div id="meta">
  <input type="hidden" name="version" value="2.1.1.001">
  <input type="hidden" name="cgi" value="/celsys/diazepam_hybrid.php">
  <input type="hidden" name="param" value="{PARAM}">
  <input type="hidden" name="url" value="{SERIES_URL}">
  <input type="hidden" name="colophon_url" value="{ORIGIN}/reader/finish/18592?url=x">
</div></body></html>
"""

FACE_XML = (
    "<Face><XML_Version>4.8.0</XML_Version><ContentFrame><Width>1000</Width><Height>1422</Height></ContentFrame>"
    "<ContentType>3</ContentType><TotalPage>2</TotalPage><Version>4.3</Version>"
    "<Scramble><Width>4</Width><Height>4</Height></Scramble><Binding>0</Binding><StartPage>1</StartPage>"
    "<OptionId>Webtoon</OptionId></Face>"
)
FACE_ERROR_XML = "<Result><Code>2004</Code><Content>not exist file</Content></Result>"


def page_xml(number, *, table=TABLE, scrambled=True):
    """The page description the CGI answers `mode=8&file=NNNN.xml` with."""
    scramble = ",".join(str(value) for value in table) if table else ""
    return (
        f"<Page><PageNo>{number}</PageNo><Sheet><X>1</X><Y>1</Y></Sheet><PartCount>1</PartCount>"
        f"<TotalPartSize>1197959</TotalPartSize><Part><Kind scramble='{int(scrambled)}' No='0000'>1</Kind></Part>"
        f"<StepRect><X>0</X><Y>0</Y><Width>1000</Width><Height>1422</Height></StepRect><StepCount>0</StepCount>"
        f"<Scramble>{scramble}</Scramble></Page>"
    )


def episode_item(episode_id, title, *, free=True):
    button = (
        f'<form data-api="reader"><input type="hidden" name="ebook_id" value="{episode_id}">'
        '<button class="btn-free">無料</button></form>'
        if free
        else f'<button js-button class="btn-rental--both" data-modal-source="{ORIGIN}/shop/rental/{episode_id}"'
        f' data-id="{episode_id}" js-modal>50</button>'
    )
    return f"""
<div js-shop-item class="shop-item--episode" id="ep{episode_id}" data-id="{episode_id}">
  <div class="shop-item-info"><span class="shop-item-info-name">{title}</span>
    <div class="shop-item-info-meta">
      <span class="shop-item-info-release">公開：2025/12/{episode_id % 28 + 1}</span></div>
    <ul class="shop-item-btnset"><li class="shop-item-btn">{button}</li></ul></div>
</div>
"""


def series_page(items):
    return f"""
<html><head><title>コーヴァ -KOHVA- - WEB読み | ファイアCROSS</title></head><body>
<main class="main"><h1 class="ebook-series-title sp-px-10">コーヴァ -KOHVA-</h1>
<ul class="ebook-series-author sp-px-10">
<li class="ebook-series-author-item"><span class="ebook-series-author-type">漫画</span>
<a href="https://firecross.jp/search?t=1&amp;author=Konata">Konata</a></li>
</ul>
<div class="ebookSeries_episodeList">{"".join(items)}</div></main></body></html>
"""


def cgi_routes(fake_response, *, face=FACE_XML, pages=None):
    """Routes for the reader CGI: `face.xml`, then the page XMLs by file name."""
    pages = pages if pages is not None else {"0000.xml": page_xml(0), "0001.xml": page_xml(1)}
    routes = {"file=face.xml": fake_response(text=face)}
    for name, xml in pages.items():
        routes[f"file={name}&"] = fake_response(text=xml)
    return routes


def series_routes(fake_response):
    """The work page, one page of three episodes then an empty one: read for the previous episode."""
    listed = series_page(
        [episode_item(18592, "第1話"), episode_item(18665, "第2話"), episode_item(18810, "第3話", free=False)]
    )
    return {"/ebook/series/596": [fake_response(text=listed), fake_response(text=series_page([]))]}


def open_reader_routes(fake_response, *, colophon=None):
    """Routes from the colophon down to the page XMLs of a free episode."""
    return {
        "/reader/colophon/18592": fake_response(
            text=colophon if colophon is not None else colophon_page(next_area=FREE_NEXT)
        ),
        "/api/reader": fake_response(payload={"redirect": TOKEN_URL}),
        "/reader/18592?trial=0": fake_response(text=READER_HTML),
        **cgi_routes(fake_response),
        **series_routes(fake_response),
    }


def tiled_image(cols=4, rows=4, tile=UNIT * 3, extra=(5, 3)):
    """An image of `cols x rows` solid tiles, each a distinct colour, plus an uneven edge strip."""
    width, height = cols * tile + extra[0], rows * tile + extra[1]
    image = Image.new("RGB", (width, height), (255, 255, 255))
    for index in range(cols * rows):
        x, y = index % cols * tile, index // cols * tile
        image.paste((index * 16, 255 - index * 16, (index * 37) % 256), (x, y, x + tile, y + tile))
    return image


def shuffle(image, table, cols, rows):
    """Scramble `image` so that tile `table[i]` of the result holds tile `i` of the original."""
    width, height = image.size
    tile_w, tile_h = width // cols // UNIT * UNIT, height // rows // UNIT * UNIT
    out = image.copy()
    for destination, source in enumerate(table):
        sx, sy = source % cols * tile_w, source // cols * tile_h
        dx, dy = destination % cols * tile_w, destination // cols * tile_h
        out.paste(image.crop((dx, dy, dx + tile_w, dy + tile_h)), (sx, sy))
    return out


def jpeg_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=100, subsampling=0)
    return buffer.getvalue()


def pixel(image, x, y):
    """The RGB bytes of one pixel."""
    return image.convert("RGB").crop((x, y, x + 1, y + 1)).tobytes()


def close(got, expected, tolerance=4):
    """Whether two pixels are the same colour, give or take JPEG noise."""
    return all(abs(a - b) <= tolerance for a, b in zip(got, expected, strict=True))


# --- URLs ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        EPISODE_URL,
        f"{EPISODE_URL}/",
        TOKEN_URL,
        f"{ORIGIN}/reader/colophon/18592",
        SERIES_URL,
        f"{SERIES_URL}?page=2",
        f"{SERIES_URL}?sort=latest",
    ],
)
def test_suitable_accepts_reader_and_series_pages(url):
    assert FireCross.suitable(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://firecross.jp/reader/18592",
        "https://www.firecross.jp/reader/18592",
        "https://example.com/reader/18592",
        f"{ORIGIN}/",
        f"{ORIGIN}/ebook/series/596/book",
        f"{ORIGIN}/comic/series/596",
        f"{ORIGIN}/reader/finish/18592",
        f"{ORIGIN}/shop/rental/18810",
        f"{ORIGIN}/ebook/comics",
    ],
)
def test_suitable_rejects_other_pages(url):
    assert not FireCross.suitable(url)


def test_is_series_tells_a_work_page_from_a_reader():
    assert FireCross.is_series(SERIES_URL)
    assert FireCross.is_series(f"{SERIES_URL}?sort=latest")
    assert not FireCross.is_series(EPISODE_URL)
    assert not FireCross.is_series(f"{SERIES_URL}/book")


# --- episode() -------------------------------------------------------------


def test_episode_reads_the_titles_the_pages_and_the_next_episode(fake_session, fake_response):
    session = fake_session(open_reader_routes(fake_response))
    episode = FireCross(session).episode(EPISODE_URL)

    assert episode.url == EPISODE_URL
    assert episode.series_title == "コーヴァ -KOHVA-"
    assert episode.episode_title == "第1話"
    assert (episode.writer, episode.publisher) == ("Konata (漫画)", "ホビージャパン")
    assert (episode.published, episode.number) == (date(2025, 12, 18592 % 28 + 1), 1)
    assert (episode.prev_url, episode.next_url) == (None, NEXT_URL)
    assert episode.readable
    assert [page.url.split("?", 1)[1].split("&param=")[0] for page in episode.pages] == [
        "mode=1&file=0000_0000.bin&reqtype=0&vm=4",
        "mode=1&file=0001_0000.bin&reqtype=0&vm=4",
    ]
    assert all(page.url.startswith(f"{CGI}?") for page in episode.pages)
    assert episode.pages[0].extra == {"scramble": TABLE, "cols": 4, "rows": 4}
    assert episode.metadata == {
        "ebook_id": 18592,
        "series_url": SERIES_URL,
        "next_ebook_id": 18665,
        "locked": False,
        "total_pages": 2,
        "scramble": [4, 4],
    }
    # The colophon, the work page (for the previous episode, which the colophon does not name), then
    # the reader was opened through the API and the CGI was asked for the face and each page.
    assert session.posts == [(f"{ORIGIN}/api/reader", {"ebook_id": "18592", "vertical": "0"})]
    assert session.calls[0] == f"{ORIGIN}/reader/colophon/18592"
    assert session.calls[1:3] == [SERIES_URL, SERIES_URL]
    assert session.calls[3] == TOKEN_URL
    assert "mode=7&file=face.xml&reqtype=0&vm=4&param=" in session.calls[4]
    assert "mode=8&file=0000.xml&reqtype=0&vm=4&param=" in session.calls[5]
    assert "mode=8&file=0001.xml&reqtype=0&vm=4&param=" in session.calls[6]


def test_episode_takes_the_tokened_reader_url_and_the_colophon_url_too(fake_session, fake_response):
    for url in (TOKEN_URL, f"{ORIGIN}/reader/colophon/18592", f"{EPISODE_URL}/"):
        episode = FireCross(fake_session(open_reader_routes(fake_response))).episode(url)
        assert episode.url == EPISODE_URL
        assert len(episode.pages) == 2


def test_episode_sends_the_xsrf_cookie_back_as_a_header(fake_session, fake_response):
    session = fake_session(open_reader_routes(fake_response))
    session.cookies.set("XSRF-TOKEN", "eyJpdiI6%2BIn0%3D", domain="firecross.jp")
    headers = []
    original_post = session.post

    def post(url, data=None, **kwargs):
        headers.append(kwargs.get("headers") or {})
        return original_post(url, data=data, **kwargs)

    session.post = post
    FireCross(session).episode(EPISODE_URL)

    assert headers[0]["X-XSRF-TOKEN"] == "eyJpdiI6+In0="
    assert headers[0]["Accept"] == "application/json"


def test_episode_keeps_an_unscrambled_part_as_is(fake_session, fake_response):
    routes = open_reader_routes(fake_response)
    routes.update(
        cgi_routes(fake_response, pages={"0000.xml": page_xml(0, table=[], scrambled=False), "0001.xml": page_xml(1)})
    )
    episode = FireCross(fake_session(routes)).episode(EPISODE_URL)

    assert episode.pages[0].extra == {"scramble": [], "cols": 4, "rows": 4}
    assert episode.pages[1].extra["scramble"] == TABLE


def test_episode_locked_when_the_api_refuses_to_open_the_reader(fake_session, fake_response):
    session = fake_session(
        {
            "/reader/colophon/18810": fake_response(text=colophon_page(episode="第3話", next_area=PAID_NEXT)),
            "/api/reader": fake_response(payload={"message": "不正なアクセスです"}, status_code=HTTPStatus.BAD_REQUEST),
            **series_routes(fake_response),
        },
    )
    episode = FireCross(session).episode(LOCKED_URL)

    assert episode.url == LOCKED_URL
    assert episode.series_title == "コーヴァ -KOHVA-"
    assert episode.episode_title == "第3話"
    assert episode.pages == ()
    assert not episode.readable
    # The next episode is a paid one too, named by the rental modal instead of a form.
    assert (episode.prev_url, episode.next_url) == (NEXT_URL, f"{ORIGIN}/reader/18963")
    assert episode.metadata == {"ebook_id": 18810, "series_url": SERIES_URL, "next_ebook_id": 18963, "locked": True}
    assert session.posts == [(f"{ORIGIN}/api/reader", {"ebook_id": "18810", "vertical": "0"})]


def test_episode_locked_when_the_cgi_has_no_book(fake_session, fake_response):
    routes = open_reader_routes(fake_response)
    routes["file=face.xml"] = fake_response(text=FACE_ERROR_XML)
    episode = FireCross(fake_session(routes)).episode(EPISODE_URL)

    assert episode.pages == ()
    assert episode.next_url == NEXT_URL
    assert episode.metadata["locked"] is True
    assert episode.metadata["total_pages"] == 0


def test_episode_at_the_end_of_the_series_has_no_next(fake_session, fake_response):
    session = fake_session(
        open_reader_routes(fake_response, colophon=colophon_page(episode="第10話", next_area=LAST_NEXT))
    )
    episode = FireCross(session).episode(EPISODE_URL)

    assert episode.episode_title == "第10話"
    assert episode.next_url is None
    assert episode.metadata["next_ebook_id"] is None


def test_episode_falls_back_on_the_title_tag(fake_session, fake_response):
    html = """<html><head><title>奥付 - 第2話 - 百錬の覇王と聖約の戦乙女 - 外伝 | ファイアCROSS</title></head>
    <body><div class="colophonNextArea"></div></body></html>"""
    episode = FireCross(fake_session(open_reader_routes(fake_response, colophon=html))).episode(EPISODE_URL)

    assert episode.series_title == "百錬の覇王と聖約の戦乙女 - 外伝"
    assert episode.episode_title == "第2話"


def test_episode_raises_for_an_id_that_is_no_episode(fake_session, fake_response):
    session = fake_session(
        {"/reader/colophon/99999999": fake_response(text="<html></html>", status_code=HTTPStatus.NOT_FOUND)}
    )
    with pytest.raises(NotAnEpisodePageError):
        FireCross(session).episode(f"{ORIGIN}/reader/99999999")


def test_episode_raises_for_a_url_that_is_no_reader(fake_session, fake_response):
    with pytest.raises(NotAnEpisodePageError):
        FireCross(fake_session({})).episode(SERIES_URL)


def test_episode_raises_when_the_reader_page_has_no_meta(fake_session, fake_response):
    routes = open_reader_routes(fake_response)
    routes["/reader/18592?trial=0"] = fake_response(text="<html><body>maintenance</body></html>")
    with pytest.raises(NotAnEpisodePageError):
        FireCross(fake_session(routes)).episode(EPISODE_URL)


def test_episode_raises_on_a_server_error_from_the_api(fake_session, fake_response):
    routes = open_reader_routes(fake_response)
    routes["/api/reader"] = fake_response(payload={"message": "expired"}, status_code=419)
    with pytest.raises(HTTPStatusError):
        FireCross(fake_session(routes)).episode(EPISODE_URL)


# --- series_urls() ---------------------------------------------------------


def test_series_urls_walks_the_pages_oldest_first_and_deduplicates(fake_session, fake_response):
    first = series_page(
        [
            episode_item(18592, "第1話"),
            episode_item(18665, "第2話"),
            episode_item(18810, "第3話", free=False),
            episode_item(18592, "第1話 (listed twice)"),
        ],
    )
    second = series_page([episode_item(18963, "第4話", free=False)])
    third = series_page([])
    session = fake_session({"/ebook/series/596": [fake_response(text=page) for page in (first, second, third)]})
    urls = FireCross(session).series_urls(f"{SERIES_URL}?sort=latest")

    assert urls == [f"{ORIGIN}/reader/{i}" for i in (18592, 18665, 18810, 18963)]
    assert all(FireCross.suitable(url) for url in urls)
    # The sort query of the given URL is dropped: the pages are walked in the site's default, oldest-first order.
    assert session.calls == [SERIES_URL] * 3
    assert session.params_seen == [{"page": 1}, {"page": 2}, {"page": 3}]


def test_series_urls_stops_when_a_page_repeats_the_previous_one(fake_session, fake_response):
    page = series_page([episode_item(18592, "第1話")])
    session = fake_session({"/ebook/series/596": fake_response(text=page)})
    assert FireCross(session).series_urls(SERIES_URL) == [EPISODE_URL]
    assert len(session.calls) == 2


def test_series_urls_raises_on_an_empty_listing(fake_session, fake_response):
    session = fake_session({"/ebook/series/596": fake_response(text=series_page([]))})
    with pytest.raises(NotAnEpisodePageError):
        FireCross(session).series_urls(SERIES_URL)


def test_series_urls_rejects_a_reader_url(fake_session):
    with pytest.raises(UnsupportedUrlError):
        FireCross(fake_session({})).series_urls(EPISODE_URL)


# --- descramble() ----------------------------------------------------------


def test_descramble_restores_a_tiled_image():
    original = tiled_image()
    scrambled = shuffle(original, TABLE, 4, 4)
    assert scrambled.tobytes() != original.tobytes()

    restored = descramble(scrambled, TABLE, 4, 4)

    assert restored.size == original.size
    assert restored.tobytes() == original.tobytes()
    assert restored is not scrambled


def test_descramble_uses_the_largest_tile_that_is_a_multiple_of_eight():
    # 1000 x 1422 in a 4 x 4 grid: tiles of 248 x 352, the rest untouched.
    image = Image.new("RGB", (1000, 1422), (0, 0, 0))
    image.paste((255, 0, 0), (0, 0, 248, 352))  # tile 0
    table = [1, 0, *range(2, 16)]  # swap tiles 0 and 1
    restored = descramble(image, table, 4, 4)

    assert pixel(restored, 248, 0) == b"\xff\x00\x00"
    assert pixel(restored, 0, 0) == b"\x00\x00\x00"
    assert pixel(restored, 247, 351) == b"\x00\x00\x00"
    assert pixel(restored, 495, 351) == b"\xff\x00\x00"
    assert pixel(restored, 496, 0) == b"\x00\x00\x00"


@pytest.mark.parametrize(
    ("table", "cols", "rows", "size"),
    [
        ([], 4, 4, (100, 100)),
        (TABLE, 4, 4, (31, 100)),
    ],
)
def test_descramble_returns_the_image_as_is_when_it_cannot_be_tiled(table, cols, rows, size):
    image = Image.new("RGB", size, (1, 2, 3))
    assert descramble(image, table, cols, rows) is image


# --- image() / download ----------------------------------------------------


def test_image_descrambles_a_scrambled_page_and_sends_the_referer(fake_session, fake_response):
    original = tiled_image()
    routes = open_reader_routes(fake_response)
    routes["mode=1&file=0000_0000.bin"] = fake_response(
        content=jpeg_bytes(shuffle(original, TABLE, 4, 4)),
        content_type="image/jpeg",
    )
    session = fake_session(routes)
    extractor = FireCross(session)
    episode = extractor.episode(EPISODE_URL)

    image = extractor.image(episode.pages[0], episode)

    assert image.size == original.size
    # JPEG at quality 100 is close enough for solid tiles to land on their colours.
    for index in range(16):
        x, y = index % 4 * (UNIT * 3) + 4, index // 4 * (UNIT * 3) + 4
        assert close(pixel(image, x, y), pixel(original, x, y)), index
    assert session.headers_seen[-1]["Referer"] == EPISODE_URL


def test_image_hands_an_unscrambled_page_over_as_is(fake_session, fake_response):
    original = tiled_image()
    routes = open_reader_routes(fake_response)
    routes.update(
        cgi_routes(fake_response, pages={"0000.xml": page_xml(0, table=[], scrambled=False), "0001.xml": page_xml(1)})
    )
    routes["mode=1&file=0000_0000.bin"] = fake_response(content=jpeg_bytes(original), content_type="image/jpeg")
    extractor = FireCross(fake_session(routes))
    episode = extractor.episode(EPISODE_URL)

    image = extractor.image(episode.pages[0], episode)

    assert image.size == original.size
    assert close(pixel(image, 4, 4), pixel(original, 4, 4))  # tile 0 is still at the top left


# --- login() ---------------------------------------------------------------

LOGIN_FORM = """
<html><body><main><form action="https://firecross.jp/login" method="post">
  <input type="hidden" name="_token" value="8jWPEV5u0MZug7lsjquNiYl15DKneCbfHbdTSvo0">
  <input class="form-text" id="input-email" name="email" type="email" required>
  <input class="form-text" id="input-password" name="password" type="password" required>
  <input class="form-checkbox" name="remember_me" type="checkbox" value="1" checked>
  <button class="btn--simple--arrow">ログイン</button>
</form></main></body></html>
"""
LOGIN_REFUSED = LOGIN_FORM.replace(
    "<form", '<p class="form-message--error">メールアドレス又はパスワードに誤りがあります</p><form', 1
)
LOGGED_IN = "<html><head><title>ファイアCROSS</title></head><body><a href='/logout'>ログアウト</a></body></html>"


def test_login_posts_the_form_with_its_csrf_token(fake_session, fake_response):
    session = fake_session({"/login": [fake_response(text=LOGIN_FORM), fake_response(text=LOGGED_IN)]})
    FireCross(session).login(EPISODE_URL, "reader@example.com", "pw")

    assert session.posts == [
        (
            f"{ORIGIN}/login",
            {
                "_token": "8jWPEV5u0MZug7lsjquNiYl15DKneCbfHbdTSvo0",
                "email": "reader@example.com",
                "password": "pw",
                "remember_me": "1",
            },
        ),
    ]


def test_login_raises_when_the_site_shows_the_form_again_with_an_error(fake_session, fake_response):
    session = fake_session({"/login": [fake_response(text=LOGIN_FORM), fake_response(text=LOGIN_REFUSED)]})
    with pytest.raises(LoginError):
        FireCross(session).login(EPISODE_URL, "reader@example.com", "wrong")


def test_login_raises_when_the_site_shows_the_form_again_without_a_message(fake_session, fake_response):
    session = fake_session({"/login": fake_response(text=LOGIN_FORM)})
    with pytest.raises(LoginError):
        FireCross(session).login(EPISODE_URL, "reader@example.com", "wrong")


# --- the real site ---------------------------------------------------------

# One episode per known host, free to read without an account.
TEST_URLS: dict[str, str] = {
    "firecross.jp": EPISODE_URL,
}

# A rental in the middle of the series: the free window grows from the first episode
# (it reached 第6話 by 2026-11) and the newest one is free too, so stay clear of both ends.
SITE_LOCKED_URL = f"{ORIGIN}/reader/20474"


@pytest.mark.network
@pytest.mark.parametrize("host", TEST_URLS)
def test_site_download(tmp_path, host):
    result = Downloader(FireCross(), tmp_path, only_first=True).download(TEST_URLS[host])
    assert result.status == "saved"
    assert (result.save_dir / "0.jpg").stat().st_size > 0
    assert result.episode.series_title == "コーヴァ -KOHVA-"
    assert result.episode.next_url == NEXT_URL


@pytest.mark.network
def test_site_series_lists_episodes_oldest_first():
    urls = FireCross().series_urls(SERIES_URL)
    assert urls[0] == EPISODE_URL
    assert len(urls) > 2
    assert all(FireCross.suitable(url) for url in urls)


@pytest.mark.network
def test_site_locks_a_rental_without_an_account():
    episode = FireCross().episode(SITE_LOCKED_URL)
    assert episode.pages == ()
    assert episode.episode_title == "第9話"
    assert episode.next_url is not None
