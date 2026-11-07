"""Save the pages an extractor hands over, one directory per episode, under one per site."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args
from urllib.parse import urlparse

from cbz import ComicInfo, Manga, PageInfo
from cbz import Format as ComicFormat
from pathvalidate import sanitize_filename

if TYPE_CHECKING:
    from .extractor import Episode, Extractor

#: The image format each page is saved as.
Format = Literal["jpg", "png", "webp"]

#: What `Downloader` reports the pages of an episode with: the episode, how
#: many pages are written, how many there are. Called before the first page
#: and after each one.
Progress = Callable[["Episode", int, int], None]

#: The directory under a series where `cbz=True` puts its archives.
CBZ_DIR = "_cbz"

#: What became of an episode: written, left alone because it was already
#: there, or locked behind a purchase, a wait or a login.
Status = Literal["saved", "exists", "locked"]


@dataclass(frozen=True)
class Result:
    """What `Downloader.download()` did with one episode."""

    episode: Episode
    save_dir: Path
    status: Status
    #: The `.cbz` the pages were packed into, when asked for; None otherwise.
    archive: Path | None = None

    @property
    def saved(self) -> bool:
        """Whether pages, or the archive, were written this time."""
        return self.status == "saved"


class Downloader:
    """Write episodes to `<save_path>/<host>/<series>/<episode>/<page>.jpg`, and pack them into `.cbz` on request."""

    def __init__(
        self,
        extractor: Extractor,
        save_path: str | Path = ".",
        *,
        overwrite: bool = False,
        only_first: bool = False,
        save_metadata: bool = False,
        progress: Progress | None = None,
        fmt: Format = "jpg",
        cbz: bool = False,
    ) -> None:
        """Build a downloader.

        Args:
            extractor: The extractor to read episodes with.
            save_path: Directory to build `<host>/<series>/<episode>/` under.
            overwrite: Download again even if the directory already exists.
            only_first: Stop after the first page.
            save_metadata: Also write `metadata.json` next to the pages.
            progress: Where to report each page written; see `Progress`.
            fmt: The image format to save each page as.
            cbz: Also pack the saved pages into `<host>/<series>/_cbz/<episode>.cbz`,
                with a `ComicInfo.xml` naming the episode. Pages already on disk are
                packed as they are, without downloading them again.
        """
        self.extractor = extractor
        self.save_path = Path(save_path)
        self.overwrite = overwrite
        self.only_first = only_first
        self.save_metadata = save_metadata
        self.progress = progress
        self.fmt = fmt
        self.cbz = cbz

    def download(self, url: str) -> Result:
        """Download one episode.

        Args:
            url: The episode URL.

        Returns:
            The episode, the directory it belongs in, and what was done.
        """
        episode = self.extractor.episode(url)
        series_dir = self.save_path / self._site(episode) / _dirname(episode.series_title)
        stem = _dirname(episode.episode_title)
        save_dir = series_dir / stem
        archive = series_dir / CBZ_DIR / f"{stem}.cbz" if self.cbz else None
        pages_there = save_dir.exists() and not self.overwrite
        archive_there = archive is None or (archive.exists() and not self.overwrite)
        if pages_there and archive_there:
            return Result(episode, save_dir, "exists", archive)

        if not pages_there:
            if not episode.readable:
                return Result(episode, save_dir, "locked", archive)
            # Written aside and moved into place once complete, so that an episode cut
            # short (a failed request, Ctrl-C) is not taken for saved on the next run.
            partial = save_dir.with_name(f"_{save_dir.name}.part")
            if partial.exists():
                shutil.rmtree(partial)
            partial.mkdir(parents=True)
            if self.save_metadata:
                (partial / "metadata.json").write_text(
                    # `default` is for the date; everything else the extractors hand over is plain JSON.
                    json.dumps(asdict(episode), indent=4, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            self._save_pages(episode, partial)
            if save_dir.exists():
                # `overwrite`: the new files replace the old ones of the same name.
                for path in partial.iterdir():
                    path.replace(save_dir / path.name)
                partial.rmdir()
            else:
                partial.rename(save_dir)
        if archive is not None and not archive_there:
            self._pack(episode, save_dir, archive)
        return Result(episode, save_dir, "saved", archive)

    def _site(self, episode: Episode) -> str:
        """The directory a site's episodes go under: its host, or the extractor's name without one."""
        return urlparse(episode.url).hostname or self.extractor.NAME

    def _save_pages(self, episode: Episode, save_dir: Path) -> None:
        """One image file per page, numbered from 0 and padded to the page count."""
        wanted = episode.pages[:1] if self.only_first else episode.pages
        width = len(str(len(wanted)))
        report = self.progress or (lambda _episode, _done, _total: None)
        report(episode, 0, len(wanted))
        for index, page in enumerate(wanted):
            image = self.extractor.image(page, episode)
            if image.mode not in _MODES[self.fmt]:
                image = image.convert("RGBA" if self.fmt != "jpg" and "A" in image.mode else "RGB")
            image.save(save_dir / f"{index:0{width}d}.{self.fmt}", quality=95)
            report(episode, index + 1, len(wanted))

    @staticmethod
    def _pack(episode: Episode, save_dir: Path, archive: Path) -> None:
        """Every page image in `save_dir`, as it is, into `archive` with a `ComicInfo.xml`."""
        files = sorted(path for path in save_dir.iterdir() if path.suffix.lower() in _SUFFIXES)
        published = episode.published
        comic = ComicInfo.from_pages(
            [PageInfo.load(path) for path in files],
            title=episode.episode_title,
            series=episode.series_title,
            number=episode.number,
            writer=episode.writer,
            publisher=episode.publisher,
            year=published.year if published else None,
            month=published.month if published else None,
            day=published.day if published else None,
            web=episode.url,
            manga=Manga.YES,
            format=ComicFormat.WEB_COMIC,
            language_iso="ja",
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        comic.save(archive)


#: The image modes each format writes as they are; anything else is converted first.
_MODES: dict[Format, tuple[str, ...]] = {
    "jpg": ("RGB", "L"),
    "png": ("1", "L", "LA", "P", "RGB", "RGBA"),
    "webp": ("RGB", "RGBA"),
}

#: What `_pack` picks up out of an episode directory: the pages, not `metadata.json`.
_SUFFIXES = frozenset(f".{fmt}" for fmt in get_args(Format))


def _dirname(title: str) -> str:
    """Turn a title into a directory name, keeping a `/` in it readable."""
    return str(sanitize_filename(title.replace("/", "／"))) or "_"
