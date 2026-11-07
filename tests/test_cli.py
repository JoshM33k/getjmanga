from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest
from cbz import ComicInfo
from httpx2 import HTTPStatusError, Request, Response

from getjmanga import __version__
from getjmanga.cli import apply_config, download, extractor_list, main, parse_args
from getjmanga.config import Config, Credentials, Work, load_config
from getjmanga.downloader import Downloader
from getjmanga.errors import LoginError, NotAnEpisodePageError
from getjmanga.extractor import Episode, Extractor, Page


def test_parse_args_defaults():
    parsed = parse_args(["https://mangabu.jp/episodes/1"])
    assert parsed.urls == ["https://mangabu.jp/episodes/1"]
    assert parsed.extractor is None
    # -b, -C, -d, -F, -K, -m and -o are left to the config file until `apply_config` settles them.
    assert (parsed.bulk, parsed.savedir, parsed.overwrite) == (None, None, None)
    assert (parsed.first, parsed.quiet, parsed.verbose) == (False, False, False)
    assert (parsed.format, parsed.cbz, parsed.kindle, parsed.metadata) == (None, None, None, None)


def test_apply_config_fills_in_what_the_command_line_left_out():
    parsed = parse_args(["https://mangabu.jp/episodes/1"])
    apply_config(
        parsed, Config(savedir=Path("/manga"), overwrite=True, bulk=True, format="webp", cbz=True, metadata=True)
    )
    assert (parsed.savedir, parsed.overwrite, parsed.bulk, parsed.both) == (Path("/manga"), True, True, False)
    assert (parsed.format, parsed.cbz, parsed.metadata) == ("webp", True, True)


def test_bulk_and_both_rule_each_other_out(capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["-b", "-B", "https://mangabu.jp/episodes/1"])
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "config", "expected"),
    [
        # The command line settles both flags, whichever one it names.
        (["-b"], Config(both=True), (True, False)),
        (["--no-bulk"], Config(both=True), (False, False)),
        (["-B"], Config(bulk=True), (False, True)),
        (["--no-both"], Config(bulk=True), (True, False)),
        (["--no-both"], Config(both=True), (False, False)),
        # Otherwise the config file does.
        ([], Config(both=True), (False, True)),
        ([], Config(), (False, False)),
    ],
)
def test_apply_config_keeps_bulk_and_both_apart(args, config, expected):
    parsed = parse_args([*args, "https://mangabu.jp/episodes/1"])
    apply_config(parsed, config)
    assert (parsed.bulk, parsed.both) == expected


def test_apply_config_falls_back_to_the_built_in_defaults():
    parsed = parse_args(["https://mangabu.jp/episodes/1"])
    apply_config(parsed, Config())
    assert (parsed.savedir, parsed.overwrite, parsed.bulk) == (".", False, False)
    assert (parsed.format, parsed.cbz, parsed.metadata) == ("jpg", False, False)


def test_the_command_line_beats_the_config_defaults():
    flags = ["-d", "here", "--no-overwrite", "--no-bulk", "-F", "png", "--no-cbz", "--no-metadata"]
    parsed = parse_args([*flags, "https://mangabu.jp/episodes/1"])
    apply_config(
        parsed, Config(savedir=Path("/manga"), overwrite=True, bulk=True, format="webp", cbz=True, metadata=True)
    )
    assert (parsed.savedir, parsed.overwrite, parsed.bulk) == ("here", False, False)
    assert (parsed.format, parsed.cbz, parsed.metadata) == ("png", False, False)


def test_parse_args_takes_several_urls():
    parsed = parse_args(["https://a/1", "https://b/2", "-e", "comici"])
    assert parsed.urls == ["https://a/1", "https://b/2"]
    assert parsed.extractor == "comici"


def test_no_arguments_print_the_help(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 0
    assert "usage: getjmanga" in capsys.readouterr().out


def test_parse_args_wants_a_url_unless_listing(capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["-q"])
    assert excinfo.value.code == 2
    assert "required: url" in capsys.readouterr().err


def test_help_prints_defaults_for_options_only(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "(default: None)" not in out
    assert "--savedir DIR" in out
    assert "--no-overwrite" in out
    assert "--quiet" in out
    assert "(default: False)" in out


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_list_extractors_names_every_extractor_and_its_hosts(capsys):
    main(["--list-extractors"])
    out = capsys.readouterr().out
    assert out == extractor_list() + "\n"
    assert "gigaviewer:" in out
    assert "comici:" in out
    assert "piccoma:" in out
    assert "https://shonenjumpplus.com" in out
    assert "https://<host>/episodes/<id>" in out
    assert "  config: [site.comici-plus]" in out
    assert "  config: [site.piccoma]" in out


class Recording(Extractor):
    """Walks a canned chain of three episodes, the second of which may be locked."""

    NAME = "recording"
    HOSTS = ("mangabu.jp",)
    instances: ClassVar[list[Recording]] = []
    locked: ClassVar[set[str]] = set()
    missing: ClassVar[set[str]] = set()

    def __init__(self, session=None):
        super().__init__(session)
        self.episodes: list[str] = []
        self.logins: list[tuple[str, str, str]] = []
        self.images = 0
        Recording.instances.append(self)

    @classmethod
    def is_series(cls, url):
        return "/series/" in url

    def series_urls(self, url):
        return [f"https://mangabu.jp/episodes/feed{index}" for index in range(3)]

    def login(self, url, username, password):
        if password == "wrong":
            msg = "refused"
            raise LoginError(msg)
        self.logins.append((url, username, password))

    def episode(self, url):
        self.episodes.append(url)
        if url in Recording.missing:
            msg = f"no viewer on {url}"
            raise NotAnEpisodePageError(msg)
        index = len(self.episodes)
        next_url = f"https://mangabu.jp/episodes/{index}" if index < 3 else None
        # The previous episode counts down from the URL's own number, unlike the next one.
        tail = url.rsplit("/", 1)[1]
        number = int(tail) if tail.isdigit() else 0
        prev_url = f"https://mangabu.jp/episodes/{number - 1}" if number else None
        pages = () if url in Recording.locked else (Page(url=f"{url}/0.jpg"),)
        return Episode(
            url=url,
            series_title="S",
            episode_title=f"ep{index}",
            pages=pages,
            next_url=next_url,
            prev_url=prev_url,
        )

    def image(self, page, episode):
        from PIL import Image  # noqa: PLC0415

        self.images += 1
        return Image.new("RGB", (2, 2))


@pytest.fixture
def recording(monkeypatch, tmp_path):
    Recording.instances.clear()
    Recording.locked = set()
    Recording.missing = set()
    Recording.CONFIG_KEY = ""
    monkeypatch.setattr("getjmanga.cli.find_extractor", lambda url: Recording)
    monkeypatch.setattr("getjmanga.cli.get_extractor", lambda name: Recording)
    monkeypatch.chdir(tmp_path)
    return Recording


def test_main_downloads_a_single_episode(recording, capsys, tmp_path):
    main(["https://mangabu.jp/episodes/0"])

    extractor = recording.instances[0]
    assert extractor.episodes == ["https://mangabu.jp/episodes/0"]
    assert extractor.images == 1
    assert (tmp_path / "mangabu.jp" / "S" / "ep1" / "0.jpg").exists()
    out = capsys.readouterr().out
    assert "saved: mangabu.jp/S/ep1" in out
    assert "done." in out


def test_bulk_follows_the_next_episode_chain(recording):
    main(["-b", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].episodes == [
        "https://mangabu.jp/episodes/0",
        "https://mangabu.jp/episodes/1",
        "https://mangabu.jp/episodes/2",
    ]


def test_both_walks_back_before_going_on(recording):
    main(["-B", "https://mangabu.jp/episodes/2"])
    # Back from 2 to 1 to 0; the fake then names episode 1 next, which was seen already.
    assert recording.instances[0].episodes == [
        "https://mangabu.jp/episodes/2",
        "https://mangabu.jp/episodes/1",
        "https://mangabu.jp/episodes/0",
    ]


def test_both_stops_walking_back_at_a_page_without_a_viewer(recording, capsys):
    recording.missing = {"https://mangabu.jp/episodes/1"}
    main(["-B", "https://mangabu.jp/episodes/2"])
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/2", "https://mangabu.jp/episodes/1"]
    assert "stop: the previous episode is not readable." in capsys.readouterr().err


def test_bulk_steps_over_a_locked_episode(recording, capsys):
    recording.locked = {"https://mangabu.jp/episodes/1"}
    main(["-b", "https://mangabu.jp/episodes/0"])

    assert len(recording.instances[0].episodes) == 3
    assert "saved: mangabu.jp/S (2 episodes, 1 locked)" in capsys.readouterr().out


def test_a_single_locked_episode_is_skipped(recording, capsys):
    recording.locked = {"https://mangabu.jp/episodes/0"}
    main(["https://mangabu.jp/episodes/0"])
    assert "skip: 'ep1' needs a purchase, a wait or a login." in capsys.readouterr().err


def test_bulk_stops_where_the_chain_stops_being_readable(recording, capsys):
    recording.missing = {"https://mangabu.jp/episodes/1"}
    main(["-b", "https://mangabu.jp/episodes/0"])

    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/1"]
    assert "stop: the next episode is not readable." in capsys.readouterr().err


def test_a_first_url_without_a_viewer_fails(recording, capsys):
    recording.missing = {"https://mangabu.jp/episodes/0"}
    with pytest.raises(SystemExit) as excinfo:
        main(["https://mangabu.jp/episodes/0"])
    assert excinfo.value.code == 1
    assert "error: no viewer on" in capsys.readouterr().err


def test_a_series_downloads_every_episode_it_lists(recording, capsys):
    main(["https://mangabu.jp/series/x"])

    assert recording.instances[0].episodes == [
        "https://mangabu.jp/episodes/feed0",
        "https://mangabu.jp/episodes/feed1",
        "https://mangabu.jp/episodes/feed2",
    ]
    assert "saved: mangabu.jp/S (3 episodes)" in capsys.readouterr().out


def test_a_series_skips_what_it_cannot_read_and_warns_about_bulk(recording, capsys):
    recording.missing = {"https://mangabu.jp/episodes/feed1"}
    main(["-b", "https://mangabu.jp/series/x"])

    err = capsys.readouterr().err
    assert "-b/-B does nothing for a series" in err
    assert "skip: https://mangabu.jp/episodes/feed1 is not readable." in err
    assert len(recording.instances[0].episodes) == 3


def test_a_series_with_nothing_readable_fails(recording, capsys):
    recording.locked = {f"https://mangabu.jp/episodes/feed{index}" for index in range(3)}
    with pytest.raises(SystemExit) as excinfo:
        main(["https://mangabu.jp/series/x"])
    assert excinfo.value.code == 1
    assert "no episode in the series" in capsys.readouterr().err


def test_several_urls_share_one_extractor(recording):
    main(["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"])
    assert len(recording.instances) == 1
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"]


def test_format_and_cbz_reach_the_downloader(recording, capsys, tmp_path):
    main(["-F", "webp", "-C", "https://mangabu.jp/episodes/0"])
    assert (tmp_path / "mangabu.jp" / "S" / "ep1" / "0.webp").exists()
    assert (tmp_path / "mangabu.jp" / "S" / "_cbz" / "ep1.cbz").exists()
    assert "saved: mangabu.jp/S/ep1" in capsys.readouterr().out


def test_kindle_makes_books_of_the_series_once_everything_is_downloaded(recording, capsys, tmp_path):
    main(["-K", "-b", "https://mangabu.jp/episodes/0"])
    comic = ComicInfo.from_cbz(tmp_path / "mangabu.jp" / "S" / "_kindle" / "S 001-010.cbz")
    assert comic.title == "S 001-010"
    assert [page.bookmark for page in comic.pages] == ["ep1", "ep2", "ep3"]
    assert capsys.readouterr().out.count("book:") == 1


def test_per_book_sets_how_many_chapters_a_book_holds(recording, tmp_path):
    main(["-K", "--per-book", "2", "-b", "https://mangabu.jp/episodes/0"])
    books = sorted(path.name for path in (tmp_path / "mangabu.jp" / "S" / "_kindle").iterdir())
    assert books == ["S 001-002.cbz", "S 003-004.cbz"]


def test_kindle_leaves_a_book_alone_when_nothing_in_it_is_new(recording, capsys, tmp_path):
    main(["-K", "https://mangabu.jp/episodes/0"])
    book = tmp_path / "mangabu.jp" / "S" / "_kindle" / "S 001-010.cbz"
    book.write_bytes(b"kept")
    capsys.readouterr()
    main(["-K", "https://mangabu.jp/episodes/0"])
    assert book.read_bytes() == b"kept"
    assert "book:" not in capsys.readouterr().out


def test_kindle_writes_a_missing_book_from_chapters_already_on_disk(recording, tmp_path):
    main(["https://mangabu.jp/episodes/0"])
    main(["-K", "https://mangabu.jp/episodes/0"])
    assert recording.instances[1].images == 0
    assert (tmp_path / "mangabu.jp" / "S" / "_kindle" / "S 001-010.cbz").exists()


def test_an_existing_episode_is_skipped(recording, capsys, tmp_path):
    (tmp_path / "mangabu.jp" / "S" / "ep1").mkdir(parents=True)
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].images == 0
    assert "skipped:" in capsys.readouterr().out


def test_login_happens_once_per_site(recording, capsys):
    main(["-u", "me", "-p", "pw", "https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"])

    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "me", "pw")]
    assert capsys.readouterr().out.count("logged in as: me") == 1


def test_login_prompts_for_a_missing_password(recording, monkeypatch):
    monkeypatch.setattr("getjmanga.cli.getpass.getpass", lambda _prompt: "typed")
    main(["-u", "me", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "me", "typed")]


def test_a_refused_login_fails(recording, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["-u", "me", "-p", "wrong", "https://mangabu.jp/episodes/0"])
    assert excinfo.value.code == 1
    assert "error: refused" in capsys.readouterr().err


def test_password_without_username_warns(recording, capsys):
    main(["-p", "pw", "https://mangabu.jp/episodes/0"])
    assert "-p without -u does nothing" in capsys.readouterr().err
    assert recording.instances[0].logins == []


def test_quiet_prints_nothing(recording, capsys):
    main(["-q", "https://mangabu.jp/episodes/0"])
    assert capsys.readouterr().out == ""


def test_quiet_still_warns(recording, capsys):
    recording.missing = {"https://mangabu.jp/episodes/1"}
    main(["-q", "-b", "https://mangabu.jp/episodes/0"])
    assert capsys.readouterr().err == "[recording] stop: the next episode is not readable.\n"


def test_verbose_logs_every_step_with_a_timestamp(recording, capsys):
    recording.locked = {"https://mangabu.jp/episodes/1"}
    main(["-v", "-C", "-b", "https://mangabu.jp/episodes/0"])
    lines = capsys.readouterr().err.splitlines()
    assert all(
        re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING) +getjmanga: ", line) for line in lines
    )
    assert [line.split(": ", 1)[1] for line in lines] == [
        "[recording] get: https://mangabu.jp/episodes/0",
        "[recording] 'ep1': 1 pages",
        "[recording] page 1/1",
        "[recording] saved: mangabu.jp/S/ep1",
        "[recording] packed: mangabu.jp/S/_cbz/ep1.cbz",
        "[recording] get: https://mangabu.jp/episodes/1",
        "[recording] skip: 'ep2' needs a purchase, a wait or a login.",
        "[recording] get: https://mangabu.jp/episodes/2",
        "[recording] 'ep3': 1 pages",
        "[recording] page 1/1",
        "[recording] saved: mangabu.jp/S/ep3",
        "[recording] packed: mangabu.jp/S/_cbz/ep3.cbz",
        "[recording] saved: mangabu.jp/S (2 episodes, 1 locked)",
        "done.",
    ]


def test_quiet_and_verbose_rule_each_other_out(capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["-q", "-v", "https://mangabu.jp/episodes/1"])
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_an_unsupported_url_fails(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["https://example.com/episodes/1"])
    assert excinfo.value.code == 1
    assert "no extractor takes" in capsys.readouterr().err


def test_an_unknown_extractor_name_fails(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["-e", "nope", "https://example.com/episodes/1"])
    assert excinfo.value.code == 1
    assert "no extractor is named 'nope'" in capsys.readouterr().err


def test_download_never_visits_a_url_twice(tmp_path):
    extractor = Recording()
    downloader = Downloader(extractor, tmp_path)

    # Started at episode 2, the fake names episode 1 next, which names episode 2 again -- a loop.
    done = download(downloader, ["https://mangabu.jp/episodes/2"], series=False, bulk=True)

    assert [result.episode.url for result in done] == ["https://mangabu.jp/episodes/2", "https://mangabu.jp/episodes/1"]
    assert extractor.episodes == ["https://mangabu.jp/episodes/2", "https://mangabu.jp/episodes/1"]
    assert isinstance(downloader.save_path, Path)


# --- -s ----------------------------------------------------------------------------------


@pytest.fixture
def searched(monkeypatch, recording):
    """`-s` finds these links on any page, without fetching anything."""
    links = ["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"]
    pages = []
    monkeypatch.setattr("getjmanga.cli.search", lambda session, url, extractor=None: pages.append(url) or list(links))
    return pages


def test_search_downloads_every_link_on_the_page(recording, searched, capsys):
    main(["-s", "https://example.com/list"])
    assert searched == ["https://example.com/list"]
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"]
    assert "search: 2 links found on https://example.com/list." in capsys.readouterr().out


def test_search_steps_over_a_link_that_fails(recording, searched, capsys):
    recording.missing = {"https://mangabu.jp/episodes/0"}
    main(["-s", "https://example.com/list"])
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"]
    assert "skip: https://mangabu.jp/episodes/0: no viewer on" in capsys.readouterr().err


def test_search_with_nothing_to_download_fails(monkeypatch, recording, capsys):
    monkeypatch.setattr("getjmanga.cli.search", lambda session, url, extractor=None: [])
    with pytest.raises(SystemExit) as excinfo:
        main(["-s", "https://example.com/list"])
    assert excinfo.value.code == 1
    assert "nothing on https://example.com/list links to" in capsys.readouterr().err


def test_search_hands_dash_e_on(monkeypatch, recording):
    seen = []
    monkeypatch.setattr("getjmanga.cli.search", lambda session, url, extractor=None: seen.append(extractor) or [])
    with pytest.raises(SystemExit):
        main(["-s", "-e", "recording", "https://example.com/list"])
    assert seen == [recording]


def test_search_walks_a_closed_range_and_steps_over_a_page_that_fails(monkeypatch, recording, capsys):
    pages = {"1": ["https://mangabu.jp/episodes/1"], "3": ["https://mangabu.jp/episodes/3"]}

    def fake_search(session, url, extractor=None):
        if url.endswith("/4"):
            raise HTTPStatusError("404", request=Request("GET", url), response=Response(404))
        return pages.get(url.rsplit("/", 1)[1], [])

    monkeypatch.setattr("getjmanga.cli.search", fake_search)
    main(["-s", "https://example.com/list/[1-4]"])
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/1", "https://mangabu.jp/episodes/3"]
    err = capsys.readouterr().err
    assert "skip: https://example.com/list/2: nothing new on https://example.com/list/2 links to" in err
    assert "skip: https://example.com/list/4: " in err


def test_search_walks_an_open_range_until_a_page_has_nothing_new(monkeypatch, recording, capsys):
    # Page 3 repeats page 2, the way a list answers a page number past its end.
    pages = {"1": ["https://mangabu.jp/episodes/1"], "2": ["https://mangabu.jp/episodes/2"]}
    visited = []

    def fake_search(session, url, extractor=None):
        visited.append(url)
        return pages.get(url.rsplit("/", 1)[1], pages["2"])

    monkeypatch.setattr("getjmanga.cli.search", fake_search)
    main(["-s", "https://example.com/list/[1-]"])
    assert visited == [f"https://example.com/list/{n}" for n in (1, 2, 3)]
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/1", "https://mangabu.jp/episodes/2"]
    assert "search: stopped at https://example.com/list/3: nothing new on" in capsys.readouterr().out


def test_search_with_nothing_on_any_page_of_the_range_fails(monkeypatch, recording, capsys):
    monkeypatch.setattr("getjmanga.cli.search", lambda session, url, extractor=None: [])
    with pytest.raises(SystemExit) as excinfo:
        main(["-s", "https://example.com/list/[1-2]"])
    assert excinfo.value.code == 1
    assert "nothing on https://example.com/list/[1-2] links to" in capsys.readouterr().err


def test_search_fetches_with_the_shared_session(monkeypatch, recording, fake_session, fake_response):
    monkeypatch.setattr("getjmanga.search.EXTRACTORS", (recording,))
    session = fake_session({"example.com": fake_response(text='<a href="https://mangabu.jp/episodes/0">x</a>')})
    monkeypatch.setattr("getjmanga.cli.make_session", lambda: session)
    main(["-s", "-q", "https://example.com/list"])
    assert session.calls[0] == "https://example.com/list"
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0"]


# --- the config file --------------------------------------------------------------------


def write_config(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_config_file_signs_in_by_host(recording, isolated_config, capsys):
    write_config(isolated_config, '[site."mangabu.jp"]\nusername = "cfg"\npassword = "cfg-pw"\n')
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "cfg", "cfg-pw")]
    assert "logged in as: cfg" in capsys.readouterr().out


def test_the_config_file_signs_in_by_the_shared_key(recording, isolated_config):
    recording.CONFIG_KEY = "recording-shared"
    write_config(isolated_config, '[site.recording-shared]\nusername = "shared"\npassword = "pw"\n')
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "shared", "pw")]


def test_the_command_line_beats_the_config_file(recording, isolated_config):
    write_config(isolated_config, '[site."mangabu.jp"]\nusername = "cfg"\npassword = "cfg-pw"\n')
    main(["-u", "me", "-p", "pw", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "me", "pw")]


def test_a_config_section_without_a_password_prompts_once(recording, isolated_config, monkeypatch):
    write_config(isolated_config, '[site."mangabu.jp"]\nusername = "cfg"\n')
    prompts = []
    monkeypatch.setattr("getjmanga.cli.getpass.getpass", lambda prompt: prompts.append(prompt) or "typed")
    main(["https://mangabu.jp/episodes/0", "https://mangabu.jp/episodes/9"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "cfg", "typed")]
    assert prompts == ["password for cfg: "]


def test_a_host_without_a_section_is_not_signed_in(recording, isolated_config):
    write_config(isolated_config, '[site."other.example"]\nusername = "cfg"\npassword = "pw"\n')
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == []


def test_dash_c_names_another_config_file(recording, tmp_path):
    path = write_config(tmp_path / "alt.toml", '[site."mangabu.jp"]\nusername = "alt"\npassword = "pw"\n')
    main(["-c", str(path), "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "alt", "pw")]


def test_a_broken_config_file_fails(recording, isolated_config, capsys):
    write_config(isolated_config, "[site\n")
    with pytest.raises(SystemExit) as excinfo:
        main(["https://mangabu.jp/episodes/0"])
    assert excinfo.value.code == 1
    assert "not valid TOML" in capsys.readouterr().err
    assert recording.instances == []


def test_the_config_file_sets_the_flag_defaults(recording, isolated_config, tmp_path):
    write_config(isolated_config, f'savedir = "{tmp_path / "out"}"\nbulk = true\n')
    main(["https://mangabu.jp/episodes/0"])
    assert len(recording.instances[0].episodes) == 3
    assert (tmp_path / "out" / "mangabu.jp" / "S" / "ep1" / "0.jpg").exists()


def test_the_config_file_sets_both(recording, isolated_config):
    write_config(isolated_config, "both = true\n")
    main(["https://mangabu.jp/episodes/2"])
    assert recording.instances[0].episodes[:2] == ["https://mangabu.jp/episodes/2", "https://mangabu.jp/episodes/1"]


def test_the_config_file_sets_overwrite(recording, isolated_config, tmp_path):
    write_config(isolated_config, "overwrite = true\n")
    (tmp_path / "mangabu.jp" / "S" / "ep1").mkdir(parents=True)
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].images == 1


def test_the_config_file_sets_format_cbz_and_metadata(recording, isolated_config, tmp_path):
    write_config(isolated_config, 'format = "webp"\ncbz = true\nmetadata = true\n')
    main(["https://mangabu.jp/episodes/0"])
    assert (tmp_path / "mangabu.jp" / "S" / "ep1" / "0.webp").exists()
    assert (tmp_path / "mangabu.jp" / "S" / "ep1" / "metadata.json").exists()
    assert (tmp_path / "mangabu.jp" / "S" / "_cbz" / "ep1.cbz").exists()


def test_no_bulk_turns_the_config_default_off(recording, isolated_config):
    write_config(isolated_config, "bulk = true\n")
    main(["--no-bulk", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0"]


# --- jm config -----------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["config", "c"])
def test_config_init_writes_the_template(word, isolated_config, capsys):
    main([word, "init"])
    assert isolated_config.exists()
    assert f"created: {isolated_config}" in capsys.readouterr().out
    config = load_config()
    assert (config.savedir, config.overwrite, config.bulk, dict(config.sites)) == (Path(), False, False, {})


def test_config_init_refuses_to_overwrite(isolated_config, capsys):
    write_config(isolated_config, "bulk = true\n")
    with pytest.raises(SystemExit) as excinfo:
        main(["config", "init"])
    assert excinfo.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert load_config().bulk is True


@pytest.mark.parametrize("word", ["config", "c"])
def test_config_alone_prints_the_help(word, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([word])
    assert excinfo.value.code == 0
    assert "usage: getjmanga config" in capsys.readouterr().out


def test_config_with_only_an_option_needs_a_subcommand(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["config", "-c", "x.toml"])
    assert excinfo.value.code == 2
    assert "required: command" in capsys.readouterr().err


def test_config_site_asks_for_the_account(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: " me@example.com ")
    monkeypatch.setattr("getjmanga.cli.getpass.getpass", lambda prompt: "pw")
    main(["c", "site", "piccoma"])
    assert load_config().sites == {"piccoma": Credentials("me@example.com", "pw")}
    out, err = capsys.readouterr()
    assert "saved: [site.piccoma]" in out
    assert err == ""


def test_config_site_leaves_an_empty_password_out(isolated_config, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "me")
    monkeypatch.setattr("getjmanga.cli.getpass.getpass", lambda prompt: "")
    main(["c", "site", "shonenjumpplus.com"])
    assert load_config().sites == {"shonenjumpplus.com": Credentials("me", None)}


def test_config_site_replaces_the_section_and_keeps_the_rest(isolated_config, monkeypatch):
    write_config(isolated_config, '# mine\nbulk = true\n\n[site.piccoma]\nusername = "old"\npassword = "old-pw"\n')
    monkeypatch.setattr("builtins.input", lambda prompt: "new")
    monkeypatch.setattr("getjmanga.cli.getpass.getpass", lambda prompt: "")
    main(["c", "site", "piccoma"])
    text = isolated_config.read_text(encoding="utf-8")
    assert text.startswith("# mine\nbulk = true\n")
    assert "old" not in text
    assert load_config().sites == {"piccoma": Credentials("new", None)}


@pytest.mark.parametrize("key", ["nowhere.example", "nope"])
def test_config_site_rejects_an_unknown_key(isolated_config, monkeypatch, capsys, key):
    monkeypatch.setattr("builtins.input", lambda prompt: "me")
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "site", key])
    assert excinfo.value.code == 1
    assert f"no extractor reads [site.{key}]" in capsys.readouterr().err
    assert not isolated_config.exists()


def test_config_site_needs_a_username(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "  ")
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "site", "piccoma"])
    assert excinfo.value.code == 1
    assert "a username is needed" in capsys.readouterr().err
    assert not isolated_config.exists()


def test_config_patrol_reads_the_title_without_downloading(recording, isolated_config, capsys):
    main(["c", "patrol", "https://mangabu.jp/episodes/0"])
    assert load_config().patrol == (Work(url="https://mangabu.jp/episodes/0", title="S"),)
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/0"]
    assert recording.instances[0].images == 0
    assert f"saved: https://mangabu.jp/episodes/0 in {isolated_config}" in capsys.readouterr().out


def test_config_patrol_reads_a_series_title_off_its_first_episode(recording, isolated_config):
    main(["c", "patrol", "https://mangabu.jp/series/1"])
    assert load_config().patrol == (Work(url="https://mangabu.jp/series/1", title="S"),)
    assert recording.instances[0].episodes == ["https://mangabu.jp/episodes/feed0"]


def test_config_patrol_signs_in_with_the_config_file(recording, isolated_config):
    write_config(isolated_config, '[site."mangabu.jp"]\nusername = "me"\npassword = "pw"\n')
    main(["c", "patrol", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "me", "pw")]


def test_config_patrol_dash_s_stores_a_page_as_is(recording, isolated_config, capsys):
    main(["c", "patrol", "-s", "https://example.com/list/[1-]"])
    assert load_config().patrol == (Work(url="https://example.com/list/[1-]", search=True),)
    assert recording.instances == []


def test_config_patrol_fails_on_a_url_no_extractor_takes(isolated_config, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "patrol", "https://example.com/nothing"])
    assert excinfo.value.code == 1
    assert "no extractor takes" in capsys.readouterr().err
    assert not isolated_config.exists()


def test_config_site_stops_when_the_input_ends(isolated_config, monkeypatch, capsys):
    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "site", "piccoma"])
    assert excinfo.value.code == 1
    assert "aborted" in capsys.readouterr().err


def test_config_savedir_stores_an_absolute_path(isolated_config, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    main(["c", "savedir", "manga"])
    assert load_config().savedir == tmp_path / "manga"


def test_config_savedir_expands_the_home_directory(isolated_config):
    main(["c", "savedir", "~/manga"])
    assert load_config().savedir == Path.home() / "manga"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("overwrite", "true", True),
        ("bulk", "false", False),
        ("both", "true", True),
        ("cbz", "true", True),
        ("kindle", "true", True),
        ("metadata", "true", True),
    ],
)
def test_config_flags_take_true_or_false(isolated_config, name, value, expected, capsys):
    main(["c", name, value])
    assert getattr(load_config(), name) is expected
    assert f"saved: {name}" in capsys.readouterr().out


def test_config_format_takes_an_image_format(isolated_config, capsys):
    main(["c", "format", "webp"])
    assert load_config().format == "webp"
    assert "saved: format" in capsys.readouterr().out
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "format", "gif"])
    assert excinfo.value.code == 2


def test_config_bulk_and_both_turn_each_other_off(isolated_config):
    main(["c", "bulk", "true"])
    main(["c", "both", "true"])
    assert (load_config().bulk, load_config().both) == (False, True)
    main(["c", "bulk", "true"])
    assert (load_config().bulk, load_config().both) == (True, False)


def test_config_flags_reject_anything_else(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "bulk", "yes"])
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_config_dash_c_edits_another_file(tmp_path, isolated_config):
    path = tmp_path / "alt.toml"
    main(["c", "-c", str(path), "bulk", "true"])
    assert load_config(path).bulk is True
    assert not isolated_config.exists()


def test_config_refuses_a_broken_file(isolated_config, capsys):
    write_config(isolated_config, "[site\n")
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "bulk", "true"])
    assert excinfo.value.code == 1
    assert "not valid TOML" in capsys.readouterr().err


def test_config_check_passes_a_sound_file(isolated_config, tmp_path, capsys):
    write_config(
        isolated_config,
        f'savedir = "{tmp_path}"\nbulk = true\n[site.piccoma]\nusername = "u"\n'
        '[[patrol]]\nurl = "https://mangabu.jp/episodes/1"\n[[patrol]]\nurl = "https://example.com/"\nsearch = true\n',
    )
    main(["c", "check"])
    out, err = capsys.readouterr()
    assert out == f"ok: {isolated_config} (1 sites, 2 patrol entries)\n"
    assert err == ""


def test_config_check_points_out_what_nothing_would_act_on(isolated_config, tmp_path, capsys):
    write_config(
        isolated_config,
        f'save_dir = "x"\nsavedir = "{tmp_path / "file"}"\n[site."example.com"]\nusername = "u"\n'
        '[[patrol]]\nurl = "https://mangabu.jp/episodes/1"\n[[patrol]]\nurl = "https://mangabu.jp/episodes/1"\n'
        '[[patrol]]\nurl = "https://example.com/nothing"\n',
    )
    (tmp_path / "file").touch()
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "check"])
    assert excinfo.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert [line.removeprefix(f"{isolated_config}: ") for line in err.splitlines()] == [
        (
            "save_dir: nothing reads it; the keys are savedir, overwrite, bulk, both, format, cbz, kindle, metadata,"
            " site, patrol."
        ),
        f"savedir: {tmp_path / 'file'} is not a directory.",
        "[site.example.com]: no extractor reads it; see `getjmanga --list-extractors`.",
        "[[patrol]] https://mangabu.jp/episodes/1: listed twice.",
        "[[patrol]] https://example.com/nothing: no extractor takes it; see `getjmanga --list-extractors`.",
    ]


def test_config_check_wants_a_file(isolated_config, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "check"])
    assert excinfo.value.code == 1
    assert f"{isolated_config} does not exist" in capsys.readouterr().err


def test_config_check_refuses_a_broken_file(isolated_config, capsys):
    write_config(isolated_config, "bulk = true\nboth = true\n")
    with pytest.raises(SystemExit) as excinfo:
        main(["c", "check"])
    assert excinfo.value.code == 1
    assert "bulk and both cannot both be true" in capsys.readouterr().err


# --- -S and jm patrol ---------------------------------------------------------------------


def test_store_remembers_the_last_episode_of_a_chain(recording, isolated_config, capsys):
    main(["-S", "-b", "https://mangabu.jp/episodes/0"])
    assert load_config().patrol == (Work("https://mangabu.jp/episodes/2", "S"),)
    assert f"stored: https://mangabu.jp/episodes/2 in {isolated_config}" in capsys.readouterr().out


def test_store_stays_at_the_first_locked_episode_of_a_chain(recording, isolated_config):
    recording.locked = {"https://mangabu.jp/episodes/1"}
    main(["-S", "-b", "https://mangabu.jp/episodes/0"])
    assert len(recording.instances[0].episodes) == 3
    assert load_config().patrol == (Work("https://mangabu.jp/episodes/1", "S"),)


def test_store_with_both_stays_at_the_first_locked_episode_in_reading_order(recording, isolated_config):
    # Episode 0 is read last, walking back, but comes first in reading order.
    recording.locked = {"https://mangabu.jp/episodes/0"}
    main(["-S", "-B", "https://mangabu.jp/episodes/2"])
    assert load_config().patrol == (Work("https://mangabu.jp/episodes/0", "S"),)


def test_store_remembers_a_single_episode_as_given(recording, isolated_config):
    main(["-S", "https://mangabu.jp/episodes/0"])
    assert load_config().patrol == (Work("https://mangabu.jp/episodes/0", "S"),)


def test_store_remembers_a_series_page(recording, isolated_config):
    main(["-S", "https://mangabu.jp/series/x"])
    assert load_config().patrol == (Work("https://mangabu.jp/series/x", "S"),)


def test_store_remembers_a_searched_page(recording, searched, isolated_config):
    main(["-S", "-s", "https://example.com/list"])
    assert load_config().patrol == (Work("https://example.com/list", search=True),)


def test_store_keeps_an_entry_once(recording, isolated_config):
    main(["-S", "https://mangabu.jp/episodes/0"])
    main(["-S", "https://mangabu.jp/episodes/0"])
    assert len(load_config().patrol) == 1


def test_store_skips_what_was_not_downloaded(recording, isolated_config):
    recording.locked = {"https://mangabu.jp/episodes/0"}
    main(["-S", "https://mangabu.jp/episodes/0"])
    assert not isolated_config.exists()


def test_store_keeps_the_rest_of_the_config(recording, isolated_config):
    write_config(isolated_config, '# mine\nbulk = true\n\n[site."mangabu.jp"]\nusername = "u"\npassword = "p"\n')
    main(["-S", "https://mangabu.jp/episodes/0"])
    text = isolated_config.read_text(encoding="utf-8")
    assert text.startswith("# mine\nbulk = true\n")
    assert '[site."mangabu.jp"]' in text
    assert text.endswith('\n\n[[patrol]]\nurl = "https://mangabu.jp/episodes/2"\ntitle = "S"\n')


def test_patrol_follows_each_chain_from_where_it_left_off(recording, isolated_config, tmp_path, capsys):
    write_config(isolated_config, '[[patrol]]\nurl = "https://mangabu.jp/episodes/0"\ntitle = "S"\n')
    (tmp_path / "mangabu.jp" / "S" / "ep1").mkdir(parents=True)
    main(["patrol"])
    extractor = recording.instances[0]
    assert extractor.episodes == [f"https://mangabu.jp/episodes/{index}" for index in range(3)]
    assert extractor.images == 2
    assert load_config().patrol == (Work("https://mangabu.jp/episodes/2", "S"),)
    out = capsys.readouterr().out
    assert "saved: mangabu.jp/S (2 episodes, 1 skipped)" in out
    assert "done." in out


def test_patrol_stays_at_the_first_locked_episode(recording, isolated_config):
    recording.locked = {"https://mangabu.jp/episodes/1"}
    write_config(isolated_config, '[[patrol]]\nurl = "https://mangabu.jp/episodes/0"\n')
    main(["p"])
    assert len(recording.instances[0].episodes) == 3
    assert load_config().patrol == (Work("https://mangabu.jp/episodes/1", "S"),)


def test_patrol_downloads_every_episode_of_a_series(recording, isolated_config):
    write_config(isolated_config, '[[patrol]]\nurl = "https://mangabu.jp/series/x"\n')
    main(["p"])
    assert len(recording.instances[0].episodes) == 3
    assert load_config().patrol == (Work("https://mangabu.jp/series/x", "S"),)


def test_patrol_searches_a_page_again(recording, searched, isolated_config):
    write_config(isolated_config, '[[patrol]]\nurl = "https://example.com/list"\nsearch = true\n')
    main(["p"])
    assert searched == ["https://example.com/list"]
    assert len(recording.instances[0].episodes) == 2


def test_patrol_steps_over_an_entry_that_fails(recording, isolated_config, capsys):
    recording.missing = {"https://mangabu.jp/episodes/0"}
    write_config(
        isolated_config,
        '[[patrol]]\nurl = "https://mangabu.jp/episodes/0"\n\n[[patrol]]\nurl = "https://mangabu.jp/episodes/9"\n',
    )
    main(["p"])
    assert "skip: https://mangabu.jp/episodes/0: no viewer on" in capsys.readouterr().err
    assert recording.instances[0].episodes[-1].startswith("https://mangabu.jp/episodes/")
    assert load_config().patrol[0] == Work("https://mangabu.jp/episodes/0")


def test_patrol_with_nothing_stored_fails(isolated_config, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["p"])
    assert excinfo.value.code == 1
    assert "nothing to patrol" in capsys.readouterr().err


def test_patrol_takes_the_download_options_but_no_url(recording, isolated_config, tmp_path, capsys):
    write_config(isolated_config, '[[patrol]]\nurl = "https://mangabu.jp/episodes/0"\n')
    main(["p", "-q", "-d", str(tmp_path / "out")])
    assert (tmp_path / "out" / "mangabu.jp" / "S" / "ep1" / "0.jpg").exists()
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit) as excinfo:
        main(["p", "https://mangabu.jp/episodes/0"])
    assert excinfo.value.code == 2
