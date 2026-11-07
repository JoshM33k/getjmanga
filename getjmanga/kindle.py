"""`-K`: the chapters of a series saved on disk, as comic archives of ten chapters each, for a Kindle.

Each book is a `.cbz` for calibre to convert (to AZW3, say) and put on the
Kindle: calibre's comic conversion turns the pages grayscale and fits them to
the device it converts for, so the pages go in as they were saved. The first
page of each chapter carries the chapter's name as a `ComicInfo.xml` bookmark,
which comic readers list; calibre makes its table of contents of the pages.

A book holds the chapters numbered 1-10, 11-20 and so on, whichever of them
are on disk, so a book is named after the range and keeps its name as the
chapters come in: `<series>/_kindle/<series> 011-020.cbz`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cbz import ComicInfo, Manga, PageInfo

if TYPE_CHECKING:
    from pathlib import Path

#: How many chapters a book holds unless `--per-book` says otherwise.
PER_BOOK = 10

#: The directory under a series where the books go.
KINDLE_DIR = "_kindle"

#: The image files a chapter directory holds, as `Downloader` writes them.
_SUFFIXES = frozenset({".jpg", ".png", ".webp"})


@dataclass(frozen=True)
class Book:
    """One archive to write: where, under what title, of which series, and which chapters go in it."""

    path: Path
    title: str
    series: str
    chapters: tuple[Path, ...]


def chapters(series_dir: Path) -> list[Path]:
    """The chapter directories of a series, in reading order.

    Directories starting with `_` (`_cbz`, `_kindle`) are skipped, and so is
    anything with no page in it. The order is the names', with the numbers in
    them compared as numbers: "Chapter 9" comes before "Chapter 10".

    Args:
        series_dir: `<savedir>/<host>/<series>`.

    Returns:
        One directory per chapter.
    """
    found = [path for path in series_dir.iterdir() if path.is_dir() and not path.name.startswith("_") and pages(path)]
    return sorted(found, key=lambda path: _natural(path.name))


def pages(chapter_dir: Path) -> list[Path]:
    """The page images of one chapter, in order (`metadata.json` left out)."""
    return sorted(
        (path for path in chapter_dir.iterdir() if path.suffix.lower() in _SUFFIXES),
        key=lambda path: _natural(path.name),
    )


def plan(series_dir: Path, title: str, per_book: int = PER_BOOK) -> list[Book]:
    """The books the chapters of a series on disk go into.

    A chapter's number is the first number in its directory name ("Chapter
    112.1" is 112); a chapter with none counts by its place in the list.
    Chapters 1 to `per_book` go into the first book, and so on. With
    `per_book` 0 every chapter goes into one book, named and titled after
    the series alone: `<series>/_kindle/<series>.cbz`.

    Args:
        series_dir: `<savedir>/<host>/<series>`, whose chapter directories to read.
        title: The series title; each book is titled after it and its range.
        per_book: How many chapter numbers a book covers; 0 for the whole series.

    Returns:
        One book per range that has a chapter on disk, in order.
    """
    if per_book == 0:
        found = tuple(chapters(series_dir))
        return [Book(series_dir / KINDLE_DIR / f"{series_dir.name}.cbz", title, title, found)] if found else []
    groups: dict[int, list[Path]] = {}
    for index, chapter in enumerate(chapters(series_dir)):
        found = re.search(r"\d+", chapter.name)
        number = int(found.group()) if found else index + 1
        groups.setdefault((max(number, 1) - 1) // per_book, []).append(chapter)
    books = []
    for group, members in sorted(groups.items()):
        span = f"{group * per_book + 1:03d}-{(group + 1) * per_book:03d}"
        path = series_dir / KINDLE_DIR / f"{series_dir.name} {span}.cbz"
        books.append(Book(path, f"{title} {span}", title, tuple(members)))
    return books


def build(book: Book) -> int:
    """Write one book: every page of its chapters, in order, and a `ComicInfo.xml` naming it.

    Args:
        book: What to write and where; the file is replaced if it is there.

    Returns:
        How many pages were written.
    """
    pages_in = [
        PageInfo.load(path, bookmark=chapter.name if index == 0 else "")
        for chapter in book.chapters
        for index, path in enumerate(pages(chapter))
    ]
    comic = ComicInfo.from_pages(pages_in, title=book.title, series=book.series, manga=Manga.YES)
    book.path.parent.mkdir(parents=True, exist_ok=True)
    partial = book.path.with_name(book.path.name + ".part")
    comic.save(partial)
    partial.replace(book.path)
    return len(pages_in)


def _natural(name: str) -> list[tuple[int, int | str]]:
    """A sort key comparing the runs of digits in `name` as numbers."""
    return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", name) if part]
