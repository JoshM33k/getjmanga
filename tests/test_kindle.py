from __future__ import annotations

import zipfile

from cbz import ComicInfo
from PIL import Image

from getjmanga.kindle import build, plan


def chapter(series, name, pages=1):
    path = series / name
    path.mkdir(parents=True)
    for index in range(pages):
        Image.new("RGB", (8, 12), (index * 40, 10, 10)).save(path / f"{index}.png")
    return path


def test_build_writes_the_chapters_of_a_book_in_reading_order(tmp_path):
    series = tmp_path / "Series"
    chapter(series, "Chapter 10")
    chapter(series, "Chapter 9", pages=2)
    (series / "Chapter 9" / "metadata.json").write_text("{}")
    (series / "_cbz").mkdir()
    (series / "empty").mkdir()
    [book] = plan(series, "かぐらバチ")

    assert build(book) == 3

    assert book.path == series / "_kindle" / "Series 001-010.cbz"
    comic = ComicInfo.from_cbz(book.path)
    assert (comic.title, comic.series) == ("かぐらバチ 001-010", "かぐらバチ")
    assert [page.bookmark for page in comic.pages] == ["Chapter 9", "", "Chapter 10"]
    # The pages go in as they were saved, in reading order.
    expected = [
        (series / name / page).read_bytes()
        for name, page in [("Chapter 9", "0.png"), ("Chapter 9", "1.png"), ("Chapter 10", "0.png")]
    ]
    with zipfile.ZipFile(book.path) as archive:
        names = sorted(name for name in archive.namelist() if name != "ComicInfo.xml")
        assert [archive.read(name) for name in names] == expected
    assert not book.path.with_name("Series 001-010.cbz.part").exists()


def test_plan_groups_the_chapters_by_their_number(tmp_path):
    series = tmp_path / "Series"
    for name in ("Chapter 10", "Chapter 11", "Chapter 112.1", "Chapter 9", "Extra"):
        chapter(series, name)

    books = plan(series, "S")
    assert [(book.path.name, book.title, [path.name for path in book.chapters]) for book in books] == [
        # "Extra" has no number, so it counts by its place in the list: the fifth.
        ("Series 001-010.cbz", "S 001-010", ["Chapter 9", "Chapter 10", "Extra"]),
        ("Series 011-020.cbz", "S 011-020", ["Chapter 11"]),
        ("Series 111-120.cbz", "S 111-120", ["Chapter 112.1"]),
    ]
    assert [book.path.name for book in plan(series, "S", per_book=5)] == [
        "Series 001-005.cbz",
        "Series 006-010.cbz",
        "Series 011-015.cbz",
        "Series 111-115.cbz",
    ]


def test_plan_puts_the_whole_series_in_one_book_with_per_book_zero(tmp_path):
    series = tmp_path / "Series"
    for name in ("Chapter 10", "Chapter 9", "Chapter 112.1"):
        chapter(series, name)

    [book] = plan(series, "S", per_book=0)
    assert (book.path.name, book.title) == ("Series.cbz", "S")
    assert [path.name for path in book.chapters] == ["Chapter 9", "Chapter 10", "Chapter 112.1"]
    assert plan(tmp_path / "Series", "S", per_book=0) == [book]


def test_plan_has_no_book_for_a_series_with_no_pages(tmp_path):
    series = tmp_path / "Series"
    (series / "_cbz").mkdir(parents=True)
    assert plan(series, "Series") == []
    assert plan(series, "Series", per_book=0) == []
