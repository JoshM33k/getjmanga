from __future__ import annotations

import json
from datetime import date

import pytest
from cbz import ComicInfo
from PIL import Image

from getjmanga.downloader import Downloader
from getjmanga.extractor import Episode, Extractor, Page


class Canned(Extractor):
    """Hands out a fixed episode and paints every page a flat colour."""

    NAME = "canned"
    HOSTS = ("example.com",)

    def __init__(self, episode, mode="RGB"):
        super().__init__()
        self._episode = episode
        self.mode = mode
        self.fetched = []

    def episode(self, url):
        return self._episode

    def image(self, page, episode):
        self.fetched.append(page.url)
        return Image.new(self.mode, (4, 4), 0 if self.mode == "L" else (10, 20, 30, 40)[: len(self.mode)])


def episode(pages=3, series_title="Series", episode_title="Episode 1"):
    return Episode(
        url="https://example.com/ep/1",
        series_title=series_title,
        episode_title=episode_title,
        pages=tuple(Page(url=f"https://cdn.example/{n}.jpg", extra={"n": n}) for n in range(pages)),
        next_url="https://example.com/ep/2",
        metadata={"raw": True},
        writer="Author",
        publisher="House",
        published=date(2026, 9, 21),
        number=7,
    )


def test_download_writes_every_page_under_site_series_and_episode(tmp_path):
    result = Downloader(Canned(episode()), tmp_path).download("https://example.com/ep/1")

    assert result.status == "saved"
    assert result.saved
    assert result.save_dir == tmp_path / "example.com" / "Series" / "Episode 1"
    assert sorted(path.name for path in result.save_dir.iterdir()) == ["0.jpg", "1.jpg", "2.jpg"]


def test_download_pads_page_numbers_to_the_page_count(tmp_path):
    result = Downloader(Canned(episode(pages=11)), tmp_path).download("u")
    names = sorted(path.name for path in result.save_dir.iterdir())
    assert names[0] == "00.jpg"
    assert names[-1] == "10.jpg"


def test_download_stops_after_the_first_page_when_asked(tmp_path):
    extractor = Canned(episode())
    result = Downloader(extractor, tmp_path, only_first=True).download("u")
    assert [path.name for path in result.save_dir.iterdir()] == ["0.jpg"]
    assert extractor.fetched == ["https://cdn.example/0.jpg"]


def test_download_reports_each_page_as_it_is_written(tmp_path):
    seen = []
    Downloader(Canned(episode()), tmp_path, progress=lambda ep, done, total: seen.append((ep, done, total))).download(
        "u"
    )
    assert seen == [(episode(), 0, 3), (episode(), 1, 3), (episode(), 2, 3), (episode(), 3, 3)]


def test_download_leaves_an_existing_directory_alone(tmp_path):
    (tmp_path / "example.com" / "Series" / "Episode 1").mkdir(parents=True)
    extractor = Canned(episode())

    result = Downloader(extractor, tmp_path).download("u")

    assert result.status == "exists"
    assert not result.saved
    assert extractor.fetched == []
    # The episode was still read, so a bulk run knows where to go next.
    assert result.episode.next_url == "https://example.com/ep/2"


def test_download_writes_again_when_told_to(tmp_path):
    (tmp_path / "example.com" / "Series" / "Episode 1").mkdir(parents=True)
    result = Downloader(Canned(episode()), tmp_path, overwrite=True).download("u")
    assert result.status == "saved"


class Failing(Canned):
    """Fails on the second page, the way a dropped connection or a refused request would."""

    def image(self, page, episode):
        if len(self.fetched) == 1:
            raise OSError("connection reset")
        return super().image(page, episode)


def test_an_episode_cut_short_is_downloaded_again_on_the_next_run(tmp_path):
    with pytest.raises(OSError, match="connection reset"):
        Downloader(Failing(episode()), tmp_path).download("u")
    assert not (tmp_path / "example.com" / "Series" / "Episode 1").exists()

    result = Downloader(Canned(episode()), tmp_path).download("u")
    assert result.status == "saved"
    assert sorted(path.name for path in (tmp_path / "example.com" / "Series").iterdir()) == ["Episode 1"]
    assert sorted(path.name for path in result.save_dir.iterdir()) == ["0.jpg", "1.jpg", "2.jpg"]


def test_download_reports_an_episode_without_pages_as_locked(tmp_path):
    result = Downloader(Canned(episode(pages=0)), tmp_path).download("u")

    assert result.status == "locked"
    assert not result.save_dir.exists()


def test_download_writes_metadata_when_asked(tmp_path):
    result = Downloader(Canned(episode()), tmp_path, save_metadata=True).download("u")

    metadata = json.loads((result.save_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["series_title"] == "Series"
    assert metadata["next_url"] == "https://example.com/ep/2"
    assert metadata["metadata"] == {"raw": True}
    assert (metadata["writer"], metadata["publisher"]) == ("Author", "House")
    assert (metadata["published"], metadata["number"]) == ("2026-09-21", 7)
    assert [page["extra"]["n"] for page in metadata["pages"]] == [0, 1, 2]


@pytest.mark.parametrize("mode", ["RGBA", "P", "L"])
def test_download_saves_any_image_mode_as_jpeg(tmp_path, mode):
    result = Downloader(Canned(episode(pages=1), mode=mode), tmp_path).download("u")
    with Image.open(result.save_dir / "0.jpg") as saved:
        assert saved.format == "JPEG"


@pytest.mark.parametrize(("fmt", "mode", "saved_mode"), [("png", "RGBA", "RGBA"), ("webp", "RGBA", "RGBA")])
def test_format_writes_one_file_per_page_keeping_the_alpha_channel(tmp_path, fmt, mode, saved_mode):
    result = Downloader(Canned(episode(pages=2), mode=mode), tmp_path, fmt=fmt).download("u")

    assert result.save_dir == tmp_path / "example.com" / "Series" / "Episode 1"
    assert sorted(path.name for path in result.save_dir.iterdir()) == [f"0.{fmt}", f"1.{fmt}"]
    with Image.open(result.save_dir / f"0.{fmt}") as saved:
        assert (saved.format, saved.mode) == (fmt.upper(), saved_mode)


def test_png_converts_a_mode_png_cannot_hold(tmp_path):
    result = Downloader(Canned(episode(pages=1), mode="CMYK"), tmp_path, fmt="png").download("u")
    with Image.open(result.save_dir / "0.png") as saved:
        assert saved.mode == "RGB"


def test_cbz_packs_the_saved_pages_as_they_are_under_the_series(tmp_path):
    result = Downloader(Canned(episode()), tmp_path, fmt="png", cbz=True).download("u")

    assert result.status == "saved"
    assert result.archive == tmp_path / "example.com" / "Series" / "_cbz" / "Episode 1.cbz"
    # The pages stay where they were; the archive is one more thing.
    assert sorted(path.name for path in result.save_dir.iterdir()) == ["0.png", "1.png", "2.png"]
    assert result.archive is not None
    comic = ComicInfo.from_cbz(result.archive)
    assert [page.suffix for page in comic] == [".png"] * 3
    assert (comic.title, comic.series, comic.number, comic.web) == (
        "Episode 1",
        "Series",
        7,
        "https://example.com/ep/1",
    )
    assert (comic.writer, comic.publisher) == ("Author", "House")
    assert (comic.year, comic.month, comic.day) == (2026, 9, 21)
    assert str(comic.manga) == "Yes"
    assert str(comic.language_iso) == "ja"


def test_cbz_packs_pages_already_on_disk_without_downloading_them(tmp_path):
    save_dir = tmp_path / "example.com" / "Series" / "Episode 1"
    save_dir.mkdir(parents=True)
    for name in ("1.jpg", "0.jpg"):
        Image.new("RGB", (4, 4)).save(save_dir / name)
    (save_dir / "metadata.json").write_text("{}")
    extractor = Canned(episode())

    result = Downloader(extractor, tmp_path, cbz=True).download("u")

    assert result.status == "saved"
    assert extractor.fetched == []
    assert result.archive is not None
    comic = ComicInfo.from_cbz(result.archive)
    assert [page.suffix for page in comic] == [".jpeg", ".jpeg"]


def test_cbz_leaves_an_existing_archive_alone(tmp_path):
    (tmp_path / "example.com" / "Series" / "Episode 1").mkdir(parents=True)
    (tmp_path / "example.com" / "Series" / "_cbz").mkdir()
    (tmp_path / "example.com" / "Series" / "_cbz" / "Episode 1.cbz").write_bytes(b"")
    extractor = Canned(episode())

    result = Downloader(extractor, tmp_path, cbz=True).download("u")

    assert result.status == "exists"
    assert extractor.fetched == []
    assert result.archive == tmp_path / "example.com" / "Series" / "_cbz" / "Episode 1.cbz"


def test_a_locked_episode_gets_no_archive(tmp_path):
    result = Downloader(Canned(episode(pages=0)), tmp_path, cbz=True).download("u")
    assert result.status == "locked"
    assert result.archive is not None
    assert not result.archive.exists()


def test_without_cbz_there_is_no_archive(tmp_path):
    result = Downloader(Canned(episode()), tmp_path).download("u")
    assert result.archive is None


def test_titles_are_made_safe_for_the_file_system(tmp_path):
    result = Downloader(Canned(episode(series_title="A/B: C?", episode_title="1/2")), tmp_path).download("u")
    assert result.save_dir == tmp_path / "example.com" / "A／B C" / "1／2"


def test_an_episode_without_a_host_goes_under_the_extractor_name(tmp_path):
    canned = Canned(Episode(url="ep/1", series_title="Series", episode_title="Episode 1", pages=()))
    result = Downloader(canned, tmp_path).download("u")
    assert result.save_dir == tmp_path / "canned" / "Series" / "Episode 1"


def test_an_empty_title_still_gets_a_directory(tmp_path):
    result = Downloader(Canned(episode(series_title="", episode_title="?")), tmp_path).download("u")
    assert result.save_dir == tmp_path / "example.com" / "_" / "_"
