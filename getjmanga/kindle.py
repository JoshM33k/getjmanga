"""`-K`: the chapters of a series saved on disk, as PDFs of ten chapters each, for a Kindle Scribe.

A PDF goes onto a Scribe as it is (through calibre, over USB), shows one page
per screen and can be written on. Each page is turned grayscale, which is all
the screen shows, and scaled down to fit the screen, so a book stays a fraction
of the pages' size. The chapters are bookmarked, so the Kindle's table of
contents jumps to one.

A book holds the chapters numbered 1-10, 11-20 and so on, whichever of them
are on disk, so a book is named after the range and keeps its name as the
chapters come in: `<series>/_kindle/<series> 011-020.pdf`.

The PDF is written out by hand rather than by Pillow, which holds every page
in memory until the end: here each page is encoded, written and let go.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

#: The Kindle Scribe's screen, in pixels; pages are scaled down to fit it, never up.
SCREEN = (1860, 2480)

#: The Scribe's pixel density, which sets the size a page claims to be.
DPI = 300

#: The JPEG quality each page is encoded at.
QUALITY = 85

#: How many chapters a book holds unless `--per-book` says otherwise.
PER_BOOK = 10

#: The directory under a series where the books go.
KINDLE_DIR = "_kindle"

#: The image files a chapter directory holds, as `Downloader` writes them.
_SUFFIXES = frozenset({".jpg", ".png", ".webp"})


@dataclass(frozen=True)
class Book:
    """One PDF to write: where, under what title, and which chapters go in it."""

    path: Path
    title: str
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
    Chapters 1 to `per_book` go into the first book, and so on.

    Args:
        series_dir: `<savedir>/<host>/<series>`, whose chapter directories to read.
        title: The series title; each book is titled after it and its range.
        per_book: How many chapter numbers a book covers.

    Returns:
        One book per range that has a chapter on disk, in order.
    """
    groups: dict[int, list[Path]] = {}
    for index, chapter in enumerate(chapters(series_dir)):
        found = re.search(r"\d+", chapter.name)
        number = int(found.group()) if found else index + 1
        groups.setdefault((max(number, 1) - 1) // per_book, []).append(chapter)
    books = []
    for group, members in sorted(groups.items()):
        span = f"{group * per_book + 1:03d}-{(group + 1) * per_book:03d}"
        books.append(Book(series_dir / KINDLE_DIR / f"{series_dir.name} {span}.pdf", f"{title} {span}", tuple(members)))
    return books


def build(book: Book) -> int:
    """Write one book.

    Args:
        book: What to write and where; the file is replaced if it is there.

    Returns:
        How many pages were written.
    """
    book.path.parent.mkdir(parents=True, exist_ok=True)
    partial = book.path.with_name(book.path.name + ".part")
    with partial.open("wb") as file:
        count = _Writer(file).write(book.title, [(chapter.name, pages(chapter)) for chapter in book.chapters])
    partial.replace(book.path)
    return count


def _natural(name: str) -> list[tuple[int, int | str]]:
    """A sort key comparing the runs of digits in `name` as numbers."""
    return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", name) if part]


def _encode(path: Path) -> tuple[bytes, int, int]:
    """One page as a grayscale JPEG that fits the screen: the bytes, the width, the height."""
    with Image.open(path) as image:
        gray = image.convert("L")
    gray.thumbnail(SCREEN, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    gray.save(buffer, "JPEG", quality=QUALITY, optimize=True)
    return buffer.getvalue(), gray.width, gray.height


def _text(value: str) -> bytes:
    """A PDF text string: UTF-16BE behind a byte order mark, in hex, which any title survives."""
    return b"<FEFF" + value.encode("utf-16-be").hex().upper().encode() + b">"


class _Writer:
    """A PDF written one object at a time, with the cross-reference table at the end."""

    def __init__(self, file: IO[bytes]) -> None:
        self.file = file
        self.offsets: dict[int, int] = {}
        self.next = 1

    def reserve(self) -> int:
        """A number for an object that is written later."""
        number = self.next
        self.next += 1
        return number

    def put(self, number: int, body: bytes, stream: bytes | None = None) -> None:
        """Write object `number`: a dictionary or value, and a stream after it when given."""
        self.offsets[number] = self.file.tell()
        self.file.write(b"%d 0 obj\n" % number + body)
        if stream is not None:
            self.file.write(b"\nstream\n" + stream + b"\nendstream")
        self.file.write(b"\nendobj\n")

    def write(self, title: str, contents: Iterable[tuple[str, list[Path]]]) -> int:
        """The whole document: the pages, a bookmark per chapter, the title. Returns the page count."""
        self.file.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        catalog, tree, outlines, info = (self.reserve() for _ in range(4))
        kids: list[int] = []
        marks: list[tuple[str, int]] = []
        for name, files in contents:
            for index, path in enumerate(files):
                page = self._page(path, tree)
                if index == 0:
                    marks.append((name, page))
                kids.append(page)

        refs = b" ".join(b"%d 0 R" % kid for kid in kids)
        self.put(tree, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (refs, len(kids)))
        self._outlines(outlines, marks)
        self.put(info, b"<< /Title %s /Producer (getjmanga) >>" % _text(title))
        self.put(
            catalog,
            b"<< /Type /Catalog /Pages %d 0 R /Outlines %d 0 R /PageMode /UseOutlines"
            b" /ViewerPreferences << /Direction /R2L >> >>" % (tree, outlines),
        )
        self._trailer(catalog, info)
        return len(kids)

    def _page(self, path: Path, tree: int) -> int:
        """One page showing the image at `path` over the whole of it; returns the page object's number."""
        jpeg, width, height = _encode(path)
        image, content, page = self.reserve(), self.reserve(), self.reserve()
        self.put(
            image,
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray"
            b" /BitsPerComponent 8 /Filter /DCTDecode /Length %d >>" % (width, height, len(jpeg)),
            jpeg,
        )
        # The page claims the size the image has at the Scribe's density, in points.
        w, h = width * 72 / DPI, height * 72 / DPI
        draw = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (w, h)
        self.put(content, b"<< /Length %d >>" % len(draw), draw)
        self.put(
            page,
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f]"
            b" /Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>" % (tree, w, h, image, content),
        )
        return page

    def _outlines(self, outlines: int, marks: list[tuple[str, int]]) -> None:
        """The bookmarks: one per chapter, opening on its first page."""
        numbers = [self.reserve() for _ in marks]
        for index, ((name, page), number) in enumerate(zip(marks, numbers, strict=True)):
            links = b""
            if index > 0:
                links += b" /Prev %d 0 R" % numbers[index - 1]
            if index < len(numbers) - 1:
                links += b" /Next %d 0 R" % numbers[index + 1]
            self.put(
                number,
                b"<< /Title %s /Parent %d 0 R%s /Dest [%d 0 R /Fit] >>" % (_text(name), outlines, links, page),
            )
        ends = b" /First %d 0 R /Last %d 0 R" % (numbers[0], numbers[-1]) if numbers else b""
        self.put(outlines, b"<< /Type /Outlines%s /Count %d >>" % (ends, len(numbers)))

    def _trailer(self, catalog: int, info: int) -> None:
        """The cross-reference table and the trailer pointing at it."""
        start = self.file.tell()
        self.file.write(b"xref\n0 %d\n0000000000 65535 f \n" % self.next)
        for number in range(1, self.next):
            self.file.write(b"%010d 00000 n \n" % self.offsets[number])
        self.file.write(
            b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (self.next, catalog, info, start)
        )
