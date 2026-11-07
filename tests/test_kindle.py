from __future__ import annotations

import io

from PIL import Image
from pypdf import PdfReader
from pypdf.generic import Destination

from getjmanga.kindle import SCREEN, build


def chapter(series, name, *sizes):
    path = series / name
    path.mkdir(parents=True)
    for index, size in enumerate(sizes):
        Image.new("RGB", size, (200, 10, 10)).save(path / f"{index}.jpg")
    return path


def test_build_puts_every_chapter_in_one_book_in_reading_order(tmp_path):
    series = tmp_path / "Series"
    chapter(series, "Chapter 10", (800, 1200))
    chapter(series, "Chapter 9", (4000, 6000), (800, 1200))
    (series / "Chapter 9" / "metadata.json").write_text("{}")
    (series / "_cbz").mkdir()
    (series / "empty").mkdir()
    out = series / "Series.pdf"

    assert build(series, "かぐらバチ", out) == 3

    reader = PdfReader(out, strict=True)
    assert reader.metadata is not None
    assert reader.metadata.title == "かぐらバチ"
    marks = [mark for mark in reader.outline if isinstance(mark, Destination)]
    assert [mark.title for mark in marks] == ["Chapter 9", "Chapter 10"]
    assert [reader.get_destination_page_number(mark) for mark in marks] == [0, 2]
    images = [Image.open(io.BytesIO(page.images[0].data)) for page in reader.pages]
    # Grayscale throughout; scaled down to fit the screen, never up.
    assert [image.mode for image in images] == ["L", "L", "L"]
    assert [image.size for image in images] == [(1653, SCREEN[1]), (800, 1200), (800, 1200)]
    assert not out.with_name("Series.pdf.part").exists()


def test_build_writes_nothing_for_a_series_with_no_pages(tmp_path):
    series = tmp_path / "Series"
    (series / "_cbz").mkdir(parents=True)
    assert build(series, "Series", series / "Series.pdf") == 0
    assert not (series / "Series.pdf").exists()
