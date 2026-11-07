"""The config file: `$XDG_CONFIG_HOME/getjmanga/config.toml`: defaults, site credentials, what to patrol.

```toml
savedir = "~/manga"           # what `-d` defaults to
overwrite = false             # whether `-o` is on unless `--no-overwrite` is given
bulk = false                  # whether `-b` is on unless `--no-bulk` is given
both = false                  # whether `-B` is on unless `--no-both` is given; not with bulk
format = "jpg"                # what `-F` defaults to: jpg, png or webp
cbz = false                   # whether `-C` is on unless `--no-cbz` is given
kindle = false                # whether `-K` is on unless `--no-kindle` is given
metadata = false              # whether `-m` is on unless `--no-metadata` is given

[site."shonenjumpplus.com"]   # one GigaViewer site; each has an account of its own
username = "you@example.com"
password = "..."

[site.comici-plus]            # a Comici ID works on every Comici+ site
username = "comici-id"
password = "..."              # leave it out to be prompted

[site.piccoma]
username = "you@example.com"
password = "..."

[[patrol]]                    # what `jm patrol` goes through; `-S` adds to it
url = "https://shonenjumpplus.com/episode/1"   # the first episode still locked, or a series page
title = "SPY FAMILY"          # a reminder, not read

[[patrol]]
url = "https://shonenjumpplus.com/"
search = true                 # a page to `-s` again
```

A `[site.<key>]` section is looked up by the URL's hostname first, then by the
extractor's `CONFIG_KEY`, so a per-host section beats the shared one.

`jm config` writes the file: `init` lays down a commented template, `site`
asks for an account, and `savedir` / `overwrite` / `bulk` / `both` / `format` /
`cbz` / `kindle` / `metadata` set the defaults (`bulk` and `both` rule each other out: setting one
clears the other). `check` reads it back and points out what nothing would act
on: a key nothing reads, a `savedir` that is not a directory, a `[site.<key>]`
no extractor reads, a `[[patrol]]` url no extractor takes.
`-S`, `jm config patrol` and `jm patrol` keep the `[[patrol]]` entries. The
writes go through tomlkit so the comments in a hand-edited file survive.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast, get_args
from urllib.parse import urlparse

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import AoT, Array, InlineTable, Table

from .downloader import Format
from .errors import GetjmangaError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .extractor import Extractor

#: Where the config lives under `XDG_CONFIG_HOME` (`~/.config` when unset).
CONFIG_RELPATH = Path("getjmanga") / "config.toml"

#: The top-level keys that stand in for a command line flag.
Option = Literal["savedir", "overwrite", "bulk", "both", "format", "cbz", "kindle", "metadata"]

#: Every top-level key something reads; anything else is a typo, for `jm config check` to point out.
KEYS: tuple[str, ...] = (*get_args(Option), "site", "patrol")

_T = TypeVar("_T", str, bool)

#: What `init` writes. The defaults and `patrol` are real lines, not comments,
#: so that `set_option` and `-S` fill them in place instead of appending below
#: the commented-out section example, where uncommenting it would swallow them.
TEMPLATE = """\
# see `jm config -h` for details

# options
savedir = "."
overwrite = false
bulk = false
both = false
format = "jpg"
cbz = false
kindle = false
metadata = false

# targets of `jm patrol`
# patrol = [
#   { url = "https://shonenjumpplus.com/episode/13932016480028799982", title = "阿波連さんははかれない" },
#   { url = "https://shonenjumpplus.com/", search = true },
#   ...
# ]
patrol = []

# [site."foobar"]
# username = "XXXX"
# password = "XXXX"

# [site."example.com"]
# username = "XXXX"
# password = "XXXX"
"""


class ConfigError(GetjmangaError):
    """The config file cannot be read or does not have the expected shape."""


def default_config_path() -> Path:
    """Where the config file is looked for when `--config` is not given.

    Returns:
        `$XDG_CONFIG_HOME/getjmanga/config.toml`, `~/.config` standing in for an
        unset or relative `XDG_CONFIG_HOME` as the XDG spec says.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / CONFIG_RELPATH


@dataclass(frozen=True)
class Credentials:
    """What `[site.<key>]` holds."""

    username: str
    #: None means "ask for it", the way `-u` without `-p` does.
    password: str | None = None


@dataclass(frozen=True)
class Work:
    """A `[[patrol]]` entry: something to download again for what is new."""

    #: The first episode still locked (else the latest reached), a series page,
    #: or -- with `search` -- a page to scan.
    url: str
    #: The series title, as a reminder when reading the file; nothing reads it.
    title: str = ""
    #: Whether `url` is a page to `-s`, rather than to download.
    search: bool = False


@dataclass(frozen=True)
class Config:
    """The parsed config file."""

    #: `[site.<key>]` sections, by key as written.
    sites: Mapping[str, Credentials] = field(default_factory=dict)
    #: `[[patrol]]` entries, in file order.
    patrol: tuple[Work, ...] = ()
    #: The file the sections came from, or None when there was none.
    path: Path | None = None
    #: What `-d` defaults to, `~` expanded; None leaves it to the command line.
    savedir: Path | None = None
    #: Whether `-o` is on by default.
    overwrite: bool = False
    #: Whether `-b` is on by default.
    bulk: bool = False
    #: Whether `-B` is on by default; never together with `bulk`.
    both: bool = False
    #: What `-F` defaults to; None leaves it to the command line.
    format: Format | None = None
    #: Whether `-C` is on by default.
    cbz: bool = False
    #: Whether `-K` is on by default.
    kindle: bool = False
    #: Whether `-m` is on by default.
    metadata: bool = False
    #: The top-level keys nothing reads, in file order, for `jm config check` to point out.
    unknown: tuple[str, ...] = ()

    def credentials(self, extractor: type[Extractor], url: str) -> Credentials | None:
        """The credentials to sign in to `url` with.

        Args:
            extractor: The extractor `url` is handled by.
            url: The URL being downloaded.

        Returns:
            The `[site."<host>"]` section for the URL's host, else the
            `[site.<CONFIG_KEY>]` section the extractor shares across its
            hosts, else None.
        """
        host = urlparse(url).hostname or ""
        for key in (host, extractor.CONFIG_KEY):
            if key and key in self.sites:
                return self.sites[key]
        return None


def load_config(path: Path | None = None) -> Config:
    """Read the config file.

    Args:
        path: The file to read instead of `default_config_path()`.

    Returns:
        The config; an empty one when the file does not exist.

    Raises:
        ConfigError: The file is not valid TOML, or a section is not shaped as expected.
    """
    where = path if path is not None else default_config_path()
    try:
        raw = where.read_bytes()
    except FileNotFoundError:
        return Config()
    except OSError as exc:
        msg = f"cannot read {where}: {exc.strerror or exc}"
        raise ConfigError(msg) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        msg = f"{where} is not valid TOML: {exc}"
        raise ConfigError(msg) from exc
    savedir = _option(data, "savedir", str, where)
    fmt = _option(data, "format", str, where)
    if fmt is not None and fmt not in get_args(Format):
        msg = f"{where}: format must be one of {', '.join(get_args(Format))}."
        raise ConfigError(msg)
    bulk, both = _option(data, "bulk", bool, where) or False, _option(data, "both", bool, where) or False
    if bulk and both:
        msg = f"{where}: bulk and both cannot both be true; -b follows the next episodes, -B the previous ones too."
        raise ConfigError(msg)
    return Config(
        sites=_sites(data.get("site", {}), where),
        patrol=_patrol(data.get("patrol", []), where),
        path=where,
        savedir=Path(savedir).expanduser() if savedir else None,
        overwrite=_option(data, "overwrite", bool, where) or False,
        bulk=bulk,
        both=both,
        format=cast("Format | None", fmt),
        cbz=_option(data, "cbz", bool, where) or False,
        kindle=_option(data, "kindle", bool, where) or False,
        metadata=_option(data, "metadata", bool, where) or False,
        unknown=tuple(key for key in data if key not in KEYS),
    )


def _option(data: dict[str, object], name: str, kind: type[_T], where: Path) -> _T | None:
    value = data.get(name)
    if value is not None and not isinstance(value, kind):
        msg = f"{where}: {name} must be a {'string' if kind is str else 'boolean'}."
        raise ConfigError(msg)
    return value


def _sites(table: object, where: Path) -> dict[str, Credentials]:
    if not isinstance(table, dict):
        msg = f"{where}: [site] must be a table of [site.<key>] sections."
        raise ConfigError(msg)
    sites: dict[str, Credentials] = {}
    for key, section in table.items():
        if not isinstance(section, dict) or not isinstance(section.get("username"), str) or not section["username"]:
            msg = f'{where}: [site."{key}"] needs a username = "..." line.'
            raise ConfigError(msg)
        password = section.get("password")
        if password is not None and not isinstance(password, str):
            msg = f'{where}: [site."{key}"] password must be a string.'
            raise ConfigError(msg)
        sites[str(key)] = Credentials(username=section["username"], password=password)
    return sites


def _patrol(entries: object, where: Path) -> tuple[Work, ...]:
    if not isinstance(entries, list):
        msg = f"{where}: patrol must be an array of [[patrol]] tables."
        raise ConfigError(msg)
    works: list[Work] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not entry["url"]:
            msg = f'{where}: every [[patrol]] entry needs a url = "..." line.'
            raise ConfigError(msg)
        title = entry.get("title", "")
        search = entry.get("search", False)
        if not isinstance(title, str) or not isinstance(search, bool):
            msg = f"{where}: [[patrol]] title must be a string and search a boolean."
            raise ConfigError(msg)
        works.append(Work(url=entry["url"], title=title, search=search))
    return tuple(works)


# --- writing it ----------------------------------------------------------------------


def init_config(path: Path | None = None) -> Path:
    """Lay down the commented template.

    Args:
        path: The file to create instead of `default_config_path()`.

    Returns:
        The file created.

    Raises:
        ConfigError: The file is already there, or cannot be written.
    """
    where = path if path is not None else default_config_path()
    if where.exists():
        msg = f"{where} already exists."
        raise ConfigError(msg)
    _write(where, TEMPLATE)
    return where


def set_site(key: str, credentials: Credentials, path: Path | None = None) -> Path:
    """Write a `[site.<key>]` section, replacing the one already there.

    Args:
        key: The host, or the extractor's `CONFIG_KEY`.
        credentials: The account; a None password leaves the line out.
        path: The file to edit instead of `default_config_path()`.

    Returns:
        The file written.

    Raises:
        ConfigError: The file cannot be read, parsed or written.
    """
    section = tomlkit.table()
    section["username"] = credentials.username
    if credentials.password is not None:
        section["password"] = credentials.password

    def edit(document: tomlkit.TOMLDocument) -> None:
        sites = document.get("site")
        if not isinstance(sites, dict):
            sites = document["site"] = tomlkit.table(is_super_table=True)
        sites[key] = section

    return _edit(path, edit)


#: Keys that rule each other out: turning one on turns the other off.
_EXCLUSIVE: dict[str, str] = {"bulk": "both", "both": "bulk"}


def set_option(name: Option, value: str | bool, path: Path | None = None) -> Path:
    """Write a top-level key, replacing the one already there.

    Args:
        name: `savedir`, `overwrite`, `bulk`, `both`, `format`, `cbz`, `kindle` or `metadata`.
        value: A path for `savedir`, kept as given; `jpg`, `png` or `webp` for
            `format`; True or False for the others. Turning `bulk` on turns
            `both` off, and the other way round.
        path: The file to edit instead of `default_config_path()`.

    Returns:
        The file written.

    Raises:
        ConfigError: The file cannot be read, parsed or written.
    """

    def edit(document: tomlkit.TOMLDocument) -> None:
        document[name] = value
        other = _EXCLUSIVE.get(name)
        if value is True and other is not None and document.get(other) is True:
            document[other] = False

    return _edit(path, edit)


def store_work(work: Work, path: Path | None = None, *, replacing: str | None = None) -> Path:
    """Add a `[[patrol]]` entry, or bring the one for the same work up to date.

    Args:
        work: The entry to write.
        path: The file to edit instead of `default_config_path()`.
        replacing: The `url` the work was stored under before, when a chain
            moved on to a later episode.

    Returns:
        The file written.

    Raises:
        ConfigError: The file cannot be read, parsed or written.
    """
    urls = {work.url, replacing} - {None}

    def edit(document: tomlkit.TOMLDocument) -> None:
        entries = document.get("patrol")
        if not isinstance(entries, AoT | Array):
            entries = document["patrol"] = tomlkit.aot()
        for entry in entries:
            if isinstance(entry, Table | InlineTable) and entry.get("url") in urls:
                _fill(entry, work)
                return
        # The template's `patrol = []` and a hand-written `patrol = [{...}, ...]` keep
        # their shape, one entry per line; otherwise one `[[patrol]]` per work.
        if isinstance(entries, Array):
            entry: Table | InlineTable = tomlkit.inline_table()
            entries.multiline(multiline=True)
        else:
            entry = tomlkit.table()
            # A blank line before the table, unless it is the first thing in the file.
            if len(entries) or len(document.body) > 1:
                entry.trivia.indent = "\n"
        _fill(entry, work)
        entries.append(entry)

    return _edit(path, edit)


def _fill(entry: Table | InlineTable, work: Work) -> None:
    entry["url"] = work.url
    if work.title:
        entry["title"] = work.title
    if work.search:
        entry["search"] = True
    elif "search" in entry:
        del entry["search"]


def _edit(path: Path | None, edit: Callable[[tomlkit.TOMLDocument], None]) -> Path:
    """Apply `edit` to the parsed file, an empty document standing in for a missing one."""
    where = path if path is not None else default_config_path()
    try:
        document = tomlkit.parse(where.read_text(encoding="utf-8"))
    except FileNotFoundError:
        document = tomlkit.document()
    except OSError as exc:
        msg = f"cannot read {where}: {exc.strerror or exc}"
        raise ConfigError(msg) from exc
    except (UnicodeDecodeError, ParseError) as exc:
        msg = f"{where} is not valid TOML: {exc}"
        raise ConfigError(msg) from exc
    edit(document)
    _write(where, tomlkit.dumps(document))
    return where


def _write(where: Path, text: str) -> None:
    """Write the file, readable by its owner only since it holds passwords."""
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        where.touch(mode=0o600)
        where.write_text(text, encoding="utf-8")
    except OSError as exc:
        msg = f"cannot write {where}: {exc.strerror or exc}"
        raise ConfigError(msg) from exc
