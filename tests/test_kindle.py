from __future__ import annotations

import io

from PIL import Image
from pypdf import PdfReader
from pypdf.generic import Destination

from getjmanga.kindle import SCREEN, build, plan


def chapter(series, name, *sizes):
    path = series / name
    path.mkdir(parents=True)
    for index, size in enumerate(sizes or [(8, 12)]):
        Image.new("RGB", size, (200, 10, 10)).save(path / f"{index}.jpg")
    return path


def test_build_writes_the_chapters_of_a_book_in_reading_order(tmp_path):
    series = tmp_path / "Series"
    chapter(series, "Chapter 10", (800, 1200))
    chapter(series, "Chapter 9", (4000, 6000), (800, 1200))
    (series / "Chapter 9" / "metadata.json").write_text("{}")
    (series / "_cbz").mkdir()
    (series / "empty").mkdir()
    [book] = plan(series, "かぐらバチ")

    assert build(book) == 3

    assert book.path == series / "_kindle" / "Series 001-010.pdf"
    reader = PdfReader(book.path, strict=True)
    assert reader.metadata is not None
    assert reader.metadata.title == "かぐらバチ 001-010"
    marks = [mark for mark in reader.outline if isinstance(mark, Destination)]
    assert [mark.title for mark in marks] == ["Chapter 9", "Chapter 10"]
    assert [reader.get_destination_page_number(mark) for mark in marks] == [0, 2]
    images = [Image.open(io.BytesIO(page.images[0].data)) for page in reader.pages]
    # Grayscale throughout; scaled down to fit the screen, never up.
    assert [image.mode for image in images] == ["L", "L", "L"]
    assert [image.size for image in images] == [(1653, SCREEN[1]), (800, 1200), (800, 1200)]
    assert not book.path.with_name("Series 001-010.pdf.part").exists()


def test_plan_groups_the_chapters_by_their_number(tmp_path):
    series = tmp_path / "Series"
    for name in ("Chapter 10", "Chapter 11", "Chapter 112.1", "Chapter 9", "Extra"):
        chapter(series, name)

    books = plan(series, "S")
    assert [(book.path.name, book.title, [path.name for path in book.chapters]) for book in books] == [
        # "Extra" has no number, so it counts by its place in the list: the fifth.
        ("Series 001-010.pdf", "S 001-010", ["Chapter 9", "Chapter 10", "Extra"]),
        ("Series 011-020.pdf", "S 011-020", ["Chapter 11"]),
        ("Series 111-120.pdf", "S 111-120", ["Chapter 112.1"]),
    ]
    assert [book.path.name for book in plan(series, "S", per_book=5)] == [
        "Series 001-005.pdf",
        "Series 006-010.pdf",
        "Series 011-015.pdf",
        "Series 111-115.pdf",
    ]


def test_plan_has_no_book_for_a_series_with_no_pages(tmp_path):
    series = tmp_path / "Series"
    (series / "_cbz").mkdir(parents=True)
    assert plan(series, "Series") == []
