"""Command line entry point for getjmanga."""

from __future__ import annotations

import getpass
import shutil
import sys
from argparse import (
    Action,
    ArgumentDefaultsHelpFormatter,
    ArgumentParser,
    ArgumentTypeError,
    BooleanOptionalAction,
    Namespace,
    RawDescriptionHelpFormatter,
)
from pathlib import Path
from typing import TYPE_CHECKING, get_args
from urllib.parse import urlparse

from httpx2 import HTTPError

from . import __version__, kindle
from .config import (
    KEYS,
    Config,
    Credentials,
    Work,
    default_config_path,
    init_config,
    load_config,
    set_option,
    set_site,
    store_work,
)
from .console import Display, logger, setup, site
from .downloader import Downloader, Format
from .errors import GetjmangaError, NotAnEpisodePageError, NothingReadableError
from .extractors import EXTRACTORS, find_extractor, get_extractor
from .search import numbered_pages, search
from .session import make_session

if TYPE_CHECKING:
    from .downloader import Result
    from .extractor import Extractor


#: What `jm config` answers to on the command line.
CONFIG_COMMANDS = ("config", "c")
#: What `jm patrol` answers to on the command line.
PATROL_COMMANDS = ("patrol", "p")


class HelpFormatter(ArgumentDefaultsHelpFormatter, RawDescriptionHelpFormatter):
    """Show argument defaults while keeping the description's own line breaks."""

    def _get_help_string(self, action: Action) -> str | None:
        # Neither a positional nor an option that defaults to nothing has a default worth printing.
        if not action.option_strings or action.default is None:
            return action.help
        return super()._get_help_string(action)


def make_parser(prog: str, description: str, epilog: str | None = None) -> ArgumentParser:
    """An `ArgumentParser` laid out the way every command here is."""
    return ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        formatter_class=lambda prog: HelpFormatter(
            prog,
            width=shutil.get_terminal_size(fallback=(120, 50)).columns,
            max_help_position=40,
        ),
    )


def extractor_list() -> str:
    """Render every extractor with the URL shapes and the hosts it takes."""
    lines: list[str] = []
    for extractor in EXTRACTORS:
        lines.append(f"{extractor.NAME}:")
        if extractor.CONFIG_KEY:
            lines.append(f"  config: [site.{extractor.CONFIG_KEY}]")
        lines.append("  urls:")
        lines.extend(f"    - {form}" for form in extractor.URL_FORMS)
        lines.append("  hosts:")
        lines.extend(f"    - https://{host}" for host in extractor.HOSTS)
    return "\n".join(lines)


def not_negative(value: str) -> int:
    """A whole number, 0 or above, for `--per-book`."""
    number = int(value)
    if number < 0:
        msg = f"{value} is below 0"
        raise ArgumentTypeError(msg)
    return number


def parse_args(args: list[str] | None = None, *, patrol: bool = False) -> Namespace:
    """Parse the command line.

    Args:
        args: Arguments to parse instead of `sys.argv[1:]`. Used by the tests.
        patrol: Parse what came after `jm patrol`: the same options, but no
            URLs -- they come from the config file -- and nothing that only
            makes sense with one.

    Returns:
        The parsed arguments.
    """
    if patrol:
        parser = make_parser(
            "getjmanga patrol",
            "Download what is new in every [[patrol]] entry of the config file (see -S)",
        )
        parser.set_defaults(
            urls=[], search=False, store=False, bulk=None, both=None, extractor=None, list_extractors=False
        )
    else:
        parser = make_parser(
            "getjmanga",
            "Retrieve and save images from japanese web comic sites",
            epilog="extractors: "
            + ", ".join(extractor.NAME for extractor in EXTRACTORS)
            + "\n\nconfig: `%(prog)s config --help` sets up the config file"
            + "\npatrol: `%(prog)s patrol --help` downloads what is new in what -S stored",
        )
        parser.add_argument(
            "urls", metavar="url", nargs="*", help="episode url, or a series url to take every episode of"
        )
        parser.add_argument(
            "-s",
            "--search",
            action="store_true",
            help="treat each url as a web page and download what it links to instead;"
            " `[1-3]` in it means the pages numbered 1 to 3, `[1-]` every page from 1 on",
        )
        parser.add_argument(
            "-S",
            "--store",
            action="store_true",
            help="remember each url in the config file, for `%(prog)s patrol` to download what is new",
        )
        # `-b`, `-B`, `-C`, `-d`, `-F`, `-K`, `-m` and `-o` default to None so that the config file can fill them in.
        chain = parser.add_mutually_exclusive_group()
        chain.add_argument("-b", "--bulk", action=BooleanOptionalAction, help="follow every next episode")
        chain.add_argument("-B", "--both", action=BooleanOptionalAction, help="follow every previous episode too")
    parser.add_argument(
        "-d", "--savedir", metavar="DIR", help="directory to save into (default: the config's savedir, else .)"
    )
    parser.add_argument("-f", "--first", action="store_true", help="download only the first page")
    parser.add_argument(
        "-F",
        "--format",
        choices=get_args(Format),
        help="image format to save each page as (default: the config's format, else jpg)",
    )
    parser.add_argument(
        "-C",
        "--cbz",
        action=BooleanOptionalAction,
        help="also pack the saved pages into <series>/_cbz/<episode>.cbz (pages already saved are packed as they are)",
    )
    parser.add_argument(
        "-K",
        "--kindle",
        action=BooleanOptionalAction,
        help="once downloaded, put the chapters of the series saved so far into comic archives of ten chapters"
        " each, for calibre to convert for a Kindle: <series>/_kindle/<series> 001-010.cbz and so on",
    )
    parser.add_argument(
        "--per-book",
        metavar="N",
        type=not_negative,
        default=kindle.PER_BOOK,
        help="how many chapters -K puts in one book; 0 puts the whole series in <series>/_kindle/<series>.cbz",
    )
    parser.add_argument("-o", "--overwrite", action=BooleanOptionalAction, help="download again if it exists")
    parser.add_argument("-m", "--metadata", action=BooleanOptionalAction, help="save episode metadata as json")
    parser.add_argument("-u", "--username", metavar="ID", help="id or email address to log in with")
    parser.add_argument("-p", "--password", metavar="PW", help="password (prompted for if -u is given without it)")
    if not patrol:
        parser.add_argument(
            "-e",
            "--extractor",
            metavar="NAME",
            help="use this extractor instead of picking one by the url's host",
        )
    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        type=Path,
        help=f"config file holding site credentials (default: {default_config_path()})",
    )
    noise = parser.add_mutually_exclusive_group()
    noise.add_argument("-q", "--quiet", action="store_true", help="print nothing but the warnings and the errors")
    noise.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log every step with a timestamp, and every request, instead of the live display",
    )
    if not patrol:
        parser.add_argument("--list-extractors", action="store_true", help="list every extractor and exit")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    if not args and not patrol:
        parser.print_help()
        raise SystemExit(0)
    parsed = parser.parse_args(args)
    if not patrol and not parsed.urls and not parsed.list_extractors:
        parser.error("the following arguments are required: url")
    return parsed


def apply_config(parsed: Namespace, config: Config) -> None:
    """Fill in the flags the command line left out from the config file.

    Args:
        parsed: The parsed command line, updated in place.
        config: The config file.
    """
    if parsed.savedir is None:
        parsed.savedir = config.savedir if config.savedir is not None else "."
    if parsed.overwrite is None:
        parsed.overwrite = config.overwrite
    if parsed.format is None:
        parsed.format = config.format if config.format is not None else "jpg"
    if parsed.cbz is None:
        parsed.cbz = config.cbz
    if parsed.kindle is None:
        parsed.kindle = config.kindle
    if parsed.metadata is None:
        parsed.metadata = config.metadata
    # `-b` and `-B` rule each other out: one given on the command line (or turned
    # off there) settles both; otherwise the config file does, which never has both on.
    if parsed.bulk is not None:
        parsed.both = False
    elif parsed.both is not None:
        parsed.bulk = False if parsed.both else config.bulk
    else:
        parsed.bulk, parsed.both = config.bulk, config.both


def parse_config_args(args: list[str]) -> Namespace:
    """Parse `jm config ...`.

    Args:
        args: What came after `config`.

    Returns:
        The parsed arguments; `command` names the subcommand.
    """
    parser = make_parser("getjmanga config", "Set up the config file")
    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        type=Path,
        help=f"config file to edit (default: {default_config_path()})",
    )
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)
    commands.add_parser("init", help="create the file from a commented template")
    site = commands.add_parser("site", help="set an account, asking for the username and password")
    site.add_argument("key", help="the site's host, or the key `getjmanga --list-extractors` prints")
    savedir = commands.add_parser("savedir", help="set what -d defaults to")
    savedir.add_argument("dir", type=Path, help="directory to save into")
    for name, flag in (("overwrite", "-o"), ("bulk", "-b (turns both off)"), ("both", "-B (turns bulk off)")):
        setter = commands.add_parser(name, help=f"set whether {flag} is on by default")
        setter.add_argument("value", choices=("true", "false"))
    fmt = commands.add_parser("format", help="set what -F defaults to")
    fmt.add_argument("value", choices=get_args(Format), help="image format to save each page as")
    for name, flag in (("cbz", "-C"), ("kindle", "-K"), ("metadata", "-m")):
        setter = commands.add_parser(name, help=f"set whether {flag} is on by default")
        setter.add_argument("value", choices=("true", "false"))
    patrol = commands.add_parser("patrol", help="add a url for `getjmanga patrol`, without downloading it now")
    patrol.add_argument("url", help="an episode or series url, whose title is read from the site, or a page with -s")
    patrol.add_argument("-s", "--search", action="store_true", help="a web page to download the links of")
    commands.add_parser("check", help="read the file back and point out what nothing would act on")
    if not args:
        parser.print_help()
        raise SystemExit(0)
    return parser.parse_args(args)


def known_site_keys() -> set[str]:
    """Every `[site.<key>]` key some extractor reads: its hosts and its `CONFIG_KEY`."""
    keys: set[str] = set()
    for extractor in EXTRACTORS:
        keys.update(extractor.HOSTS)
        if extractor.CONFIG_KEY:
            keys.add(extractor.CONFIG_KEY)
    return keys


def check_config(config: Config) -> list[str]:
    """What in the config file nothing would act on, or would fail on.

    `load_config` has already refused what does not parse; this is the rest: a
    key nothing reads, a `savedir` that is not a directory, a `[site.<key>]` no
    extractor reads, a `[[patrol]]` url no extractor takes or that is listed twice.

    Args:
        config: The config file, as `load_config` read it.

    Returns:
        One line per problem, in file order; empty when there is none.
    """
    problems = [f"{key}: nothing reads it; the keys are {', '.join(KEYS)}." for key in config.unknown]
    if config.savedir is not None and config.savedir.exists() and not config.savedir.is_dir():
        problems.append(f"savedir: {config.savedir} is not a directory.")
    sites = known_site_keys()
    problems.extend(
        f"[site.{key}]: no extractor reads it; see `getjmanga --list-extractors`."
        for key in config.sites
        if key not in sites
    )
    seen: set[str] = set()
    for work in config.patrol:
        if work.url in seen:
            problems.append(f"[[patrol]] {work.url}: listed twice.")
        seen.add(work.url)
        if not work.search and not any(extractor.suitable(work.url) for extractor in EXTRACTORS):
            problems.append(f"[[patrol]] {work.url}: no extractor takes it; see `getjmanga --list-extractors`.")
    return problems


def check_main(path: Path | None) -> None:
    """Run `jm config check`: one line per problem on stderr, or an `ok:` line.

    Raises:
        SystemExit: The file does not exist, or `check_config` found something.
    """
    config = load_config(path)
    if config.path is None:
        where = path if path is not None else default_config_path()
        print(f"error: {where} does not exist; `getjmanga config init` creates it.", file=sys.stderr)
        raise SystemExit(1)
    problems = check_config(config)
    for problem in problems:
        print(f"{config.path}: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit(1)
    print(f"ok: {config.path} ({len(config.sites)} sites, {len(config.patrol)} patrol entries)")


def ask_credentials(key: str) -> Credentials:
    """Prompt for the account to write under `[site.<key>]`.

    Raises:
        SystemExit: No extractor reads the key, no username was typed, or the input ended.
    """
    if key not in known_site_keys():
        print(f"error: no extractor reads [site.{key}]; see `getjmanga --list-extractors`.", file=sys.stderr)
        raise SystemExit(1)
    try:
        username = input(f"username for {key}: ").strip()
        if not username:
            print("error: a username is needed.", file=sys.stderr)
            raise SystemExit(1)
        password = getpass.getpass("password (leave empty to be prompted for it each run): ")
    except EOFError:
        print("error: aborted.", file=sys.stderr)
        raise SystemExit(1) from None
    # An empty password means "ask at run time", the way a section without one does.
    return Credentials(username, password or None)


def config_main(args: list[str]) -> None:
    """Run `jm config ...`.

    Raises:
        SystemExit: The file could not be written, no username was typed, the
            site `patrol` reads a title from could not be reached, or `check`
            found something wrong.
    """
    parsed = parse_config_args(args)
    try:
        if parsed.command == "init":
            path = init_config(parsed.config)
            print("created:", path)
        elif parsed.command == "check":
            check_main(parsed.config)
        elif parsed.command == "site":
            path = set_site(parsed.key, ask_credentials(parsed.key), parsed.config)
            print(f"saved: [site.{parsed.key}] in {path}")
        elif parsed.command == "savedir":
            path = set_option("savedir", str(parsed.dir.expanduser().absolute()), parsed.config)
            print(f"saved: savedir in {path}")
        elif parsed.command == "format":
            path = set_option("format", parsed.value, parsed.config)
            print(f"saved: format in {path}")
        elif parsed.command == "patrol":
            work = Work(url=parsed.url, search=True)
            if not parsed.search:
                # Only what `extractor()` and `login()` read of a command line: no -u, no -e.
                runner = Runner(Namespace(username=None, extractor=None), load_config(parsed.config), None, setup())
                work = Work(url=parsed.url, title=runner.title(parsed.url))
            path = store_work(work, parsed.config)
            print(f"saved: {work.url} in {path}")
        else:
            path = set_option(parsed.command, parsed.value == "true", parsed.config)
            print(f"saved: {parsed.command} in {path}")
    except (GetjmangaError, HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def episode_urls(extractor: Extractor, url: str, display: Display | None = None) -> list[str]:
    """The episodes to download: everything a series lists, or the URL itself.

    Args:
        extractor: The extractor to read the series with.
        url: The URL given on the command line.
        display: Where to say how many episodes a series lists.

    Returns:
        One episode URL per download.
    """
    if not extractor.is_series(url):
        return [url]
    urls = extractor.series_urls(url)
    if display is not None:
        display.series(len(urls))
    return urls


class Walk:
    """One pass over episodes: what was read, whatever became of it."""

    def __init__(self, downloader: Downloader, display: Display, *, series: bool) -> None:
        """Set up a pass.

        Args:
            downloader: The downloader to run.
            display: Where to report each episode.
            series: The episodes come from a series listing, so a page with no
                viewer is skipped rather than ending a chain.
        """
        self.downloader = downloader
        self.display = display
        self.series = series
        #: Every episode read, in reading order.
        self.visited: list[Result] = []
        self._seen: set[str] = set()

    def visit(self, url: str, what: str) -> Result | None:
        """Download one episode.

        Args:
            url: The episode.
            what: What `url` is, for the message when it has no viewer: "the next episode".

        Returns:
            The result; None when the URL was visited already, or a chain hit
            a page with no viewer on it.

        Raises:
            NotAnEpisodePageError: The very first URL of a chain has no viewer.
        """
        if url in self._seen:
            return None
        self._seen.add(url)
        self.display.fetching(url)
        try:
            result = self.downloader.download(url)
        except NotAnEpisodePageError:
            # Locked episodes on some sites serve a purchase page with no viewer
            # on it, which is where a chain is meant to end rather than fail.
            if not self.series and not self.visited:
                raise
            if self.series:
                logger.warning("skip: %s is not readable.", url)
            else:
                logger.warning("stop: %s is not readable.", what)
            return None
        self.visited.append(result)
        self.display.finished(result)
        return result

    def chain(self, start: Result, *, back: bool) -> list[Result]:
        """Follow the next (or previous) episode from `start` as far as it goes.

        Args:
            start: Where to walk from.
            back: Follow `prev_url` instead of `next_url`.

        Returns:
            What was walked to, in walking order.
        """
        walked: list[Result] = []
        result: Result | None = start
        while result is not None:
            url = result.episode.prev_url if back else result.episode.next_url
            if not url:
                break
            result = self.visit(url, "the previous episode" if back else "the next episode")
            if result is not None:
                walked.append(result)
        return walked


def download(
    downloader: Downloader,
    queue: list[str],
    display: Display | None = None,
    *,
    series: bool,
    bulk: bool,
    back: bool = False,
) -> list[Result]:
    """Download every queued episode.

    Args:
        downloader: The downloader to run.
        queue: The episodes to download: a series listing, or one episode
            to walk a chain from.
        display: Where to report each episode; nowhere by default.
        series: The queue came from a series listing, whose episodes stand on
            their own, so no chain is walked.
        bulk: Follow each episode's next episode.
        back: Follow each episode's previous episode first.

    Returns:
        Every episode read, whatever became of it, in reading order: what
        `back` walked to comes before the episode it started from.
    """
    walk = Walk(downloader, display or Display(), series=series)
    for url in queue:
        start = walk.visit(url, "the episode")
        if series or start is None:
            continue
        if back:
            earlier = walk.chain(start, back=True)
            # Walked to from the start, but read before it.
            del walk.visited[-len(earlier) - 1 :]
            walk.visited.extend([*reversed(earlier), start])
        if bulk:
            walk.chain(start, back=False)
    return walk.visited


class Runner:
    """Drive one command line: pick extractors, sign in once per site, download."""

    def __init__(self, parsed: Namespace, config: Config, password: str | None, display: Display) -> None:
        """Build a runner.

        Args:
            parsed: The parsed command line.
            config: The config file, for the credentials `-u` did not give.
            password: The password that goes with `-u`, once `-p` and the prompt are settled.
            display: Where to report what is going on, as `setup()` handed it back.
        """
        self.parsed = parsed
        self.config = config
        self.password = password
        self.display = display
        self.session = make_session()
        # One instance per extractor class, so a chain of episodes shares its
        # cookies and a series on the same site is signed in to once.
        self._extractors: dict[type[Extractor], Extractor] = {}
        self._logged_in: set[tuple[str, str]] = set()
        self._prompted: dict[str, str] = {}
        #: The series `-K` makes books of once everything is downloaded: their directory, their
        #: title, and the chapter directories saved this time.
        self._books: dict[Path, tuple[str, set[Path]]] = {}

    def extractor(self, url: str) -> Extractor:
        """The extractor for `url`: the one `-e` named, or the first that takes it."""
        cls = get_extractor(self.parsed.extractor) if self.parsed.extractor else find_extractor(url)
        if cls not in self._extractors:
            self._extractors[cls] = cls(self.session)
        return self._extractors[cls]

    def credentials(self, extractor: Extractor, url: str) -> Credentials | None:
        """What to sign in to `url` with: `-u`/`-p` when given, else the config file's section."""
        if self.parsed.username:
            return Credentials(self.parsed.username, self.password)
        return self.config.credentials(type(extractor), url)

    def login(self, extractor: Extractor, url: str) -> None:
        """Sign in to the site `url` is on, unless that already happened or nothing says how."""
        credentials = self.credentials(extractor, url)
        if credentials is None:
            return
        key = (extractor.NAME, urlparse(url).netloc)
        if key in self._logged_in:
            return
        password = credentials.password
        if password is None:
            # A section without a password is asked for it once per account.
            if credentials.username not in self._prompted:
                self._prompted[credentials.username] = getpass.getpass(f"password for {credentials.username}: ")
            password = self._prompted[credentials.username]
        extractor.login(url, credentials.username, password)
        self._logged_in.add(key)
        logger.info("logged in as: %s", credentials.username)

    def run(self, url: str, *, bulk: bool | None = None, back: bool | None = None) -> list[Result]:
        """Download `url`: the episode, or every episode of the series.

        Args:
            url: The episode or series URL.
            bulk: Follow the next episode, instead of doing so when `-b` or `-B` says to.
            back: Follow the previous episode, instead of doing so when `-B` says to.

        Returns:
            Every episode read, in order, whatever became of it.

        Raises:
            NothingReadableError: Nothing in the series was readable.
        """
        parsed = self.parsed
        extractor = self.extractor(url)
        with site(extractor.NAME):
            self.login(extractor, url)

            # A series listing already names every episode, so there is no next episode to follow.
            series = extractor.is_series(url)
            if series and bulk is None and (parsed.bulk or parsed.both):
                logger.warning("warning: -b/-B does nothing for a series, every listed episode is downloaded.")
            downloader = Downloader(
                extractor,
                parsed.savedir,
                overwrite=parsed.overwrite,
                only_first=parsed.first,
                save_metadata=parsed.metadata,
                progress=self.display.pages,
                fmt=parsed.format,
                cbz=parsed.cbz,
            )
            self.display.work(url)
            try:
                queue = episode_urls(extractor, url, self.display)
                visited = download(
                    downloader,
                    queue,
                    self.display,
                    series=series,
                    bulk=(parsed.bulk or parsed.both) if bulk is None else bulk,
                    back=parsed.both if back is None else back,
                )
            finally:
                # Whatever ended the work, the display comes down and says what it got through.
                self.display.done()
            if parsed.kindle:
                self._want_books(visited)
            if series and all(result.status == "locked" for result in visited):
                msg = f"no episode in the series at {url} was readable."
                raise NothingReadableError(msg)
            return visited

    def _want_books(self, visited: list[Result]) -> None:
        """Note the series of `visited` for `books()`, with the chapters saved this time."""
        for result in visited:
            if result.status == "locked":
                continue
            series_dir = result.save_dir.parent
            _, saved = self._books.setdefault(series_dir, (result.episode.series_title, set()))
            if result.saved:
                saved.add(result.save_dir)

    def books(self) -> None:
        """Write the books `-K` asked for: those with a chapter saved this time, and those not there yet."""
        for series_dir, (title, saved) in self._books.items():
            for book in kindle.plan(series_dir, title, self.parsed.per_book):
                if book.path.exists() and not saved.intersection(book.chapters):
                    continue
                count = kindle.build(book)
                size = book.path.stat().st_size / 1024 / 1024
                logger.info("book: %s (%d chapters, %d pages, %.1f MB)", book.path, len(book.chapters), count, size)
        self._books.clear()

    def visit(self, work: Work, *, bulk: bool | None = None, back: bool | None = None) -> Work | None:
        """Download `work`, the way `-S` stored it, and say what to store now.

        Args:
            work: A URL to download, or -- with `search` -- a page to scan.
            bulk: Follow the next episode, instead of doing so when `-b` or `-B` says to.
            back: Follow the previous episode, instead of doing so when `-B` says to.

        Returns:
            The entry to remember `work` as. A chain moves on to the first
            episode still locked, so that the next visit tries it again, or
            to the last episode reached when none was; a series or a page
            stays. None when nothing was downloaded or found there.
        """
        if work.search:
            self.search(work.url)
            return Work(url=work.url, search=True)
        visited = self.run(work.url, bulk=bulk, back=back)
        if all(result.status == "locked" for result in visited):
            return None
        chain = not self.extractor(work.url).is_series(work.url)
        pending = next((result for result in visited if result.status == "locked"), visited[-1])
        return Work(url=pending.episode.url if chain else work.url, title=visited[-1].episode.series_title)

    def title(self, url: str) -> str:
        """Read the series title at `url` off the site, without downloading anything.

        Args:
            url: An episode URL, or a series URL whose first listed episode is read.

        Returns:
            The `series_title` of the episode.

        Raises:
            NothingReadableError: The series lists no episode to read the title from.
        """
        extractor = self.extractor(url)
        with site(extractor.NAME):
            self.login(extractor, url)
            episodes = episode_urls(extractor, url)
            if not episodes:
                msg = f"the series at {url} lists no episode."
                raise NothingReadableError(msg)
            return extractor.episode(episodes[0]).series_title

    def search(self, url: str) -> None:
        """Download every link on the page at `url` that an extractor takes.

        A `[1-3]` in the URL stands for the pages numbered 1 to 3, a `[1-]`
        for every page from 1 on, up to the first that fails or has nothing
        new. A numbered page that fails is reported and stepped over.

        Raises:
            NothingReadableError: The page, or every page of the range, links to nothing an extractor takes.
            httpx2.HTTPError: The page could not be fetched; a numbered page is only reported.
        """
        extractor = get_extractor(self.parsed.extractor) if self.parsed.extractor else None
        expanded = numbered_pages(url)
        if expanded is None:
            self._search_page(url, extractor, set())
            return
        pages, open_ended = expanded
        seen: set[str] = set()
        for page in pages:
            try:
                seen.update(self._search_page(page, extractor, seen))
            except (NothingReadableError, HTTPError) as exc:
                if not open_ended:
                    logger.warning("skip: %s: %s", page, exc)
                    continue
                logger.info("search: stopped at %s: %s", page, exc)
                break
        if not seen:
            msg = f"nothing on {url} links to a page an extractor takes."
            raise NothingReadableError(msg)

    def _search_page(self, page: str, extractor: type[Extractor] | None, seen: set[str]) -> list[str]:
        """Download what one page links to, apart from `seen`, and hand the links back.

        A link that fails is reported and stepped over, since a page links to
        more than what is readable.

        Raises:
            NothingReadableError: The page links to nothing new an extractor takes.
        """
        links = [link for link in search(self.session, page, extractor) if link not in seen]
        if not links:
            msg = f"nothing {'new ' if seen else ''}on {page} links to a page an extractor takes."
            raise NothingReadableError(msg)
        logger.info("search: %d links found on %s.", len(links), page)
        for link in links:
            try:
                self.run(link)
            except (GetjmangaError, HTTPError) as exc:
                logger.warning("skip: %s: %s", link, exc)
        return links


def make_runner(parsed: Namespace) -> Runner:
    """Build the runner for a parsed command line: set the display up, settle the password, read the config.

    Raises:
        ConfigError: The config file cannot be read.
    """
    display = setup(quiet=parsed.quiet, verbose=parsed.verbose)
    password = parsed.password
    if parsed.username and password is None:
        password = getpass.getpass("password: ")
    elif password and not parsed.username:
        logger.warning("warning: -p without -u does nothing.")
    config = load_config(parsed.config)
    apply_config(parsed, config)
    return Runner(parsed, config, password, display)


def patrol_main(args: list[str]) -> None:
    """Run `jm patrol ...`: download what is new in every `[[patrol]]` entry.

    An entry that fails is reported and stepped over; a chain entry that
    moved on is stored again at the last episode reached.

    Raises:
        SystemExit: The config file has no entries, or cannot be read.
    """
    parsed = parse_args(args, patrol=True)
    try:
        runner = make_runner(parsed)
    except GetjmangaError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    if not runner.config.patrol:
        where = runner.config.path or default_config_path()
        logger.error("nothing to patrol; download with -S first, or add [[patrol]] entries to %s.", where)
        raise SystemExit(1)

    for work in runner.config.patrol:
        logger.debug("patrol: %s", work.title or work.url)
        try:
            # A chain entry sits at the first episode still locked, so there is nothing to walk back to.
            stored = runner.visit(work, bulk=True, back=False)
            if stored is not None and stored != work:
                store_work(stored, parsed.config, replacing=work.url)
        except (GetjmangaError, HTTPError) as exc:
            logger.warning("skip: %s: %s", work.url, exc)
    runner.books()
    logger.info("done.")


def main(args: list[str] | None = None) -> None:
    """Run the command."""
    argv = sys.argv[1:] if args is None else args
    if argv and argv[0] in CONFIG_COMMANDS:
        config_main(argv[1:])
        return
    if argv and argv[0] in PATROL_COMMANDS:
        patrol_main(argv[1:])
        return
    parsed = parse_args(argv)
    if parsed.list_extractors:
        print(extractor_list())
        return

    try:
        runner = make_runner(parsed)
        for url in parsed.urls:
            stored = runner.visit(Work(url=url, search=parsed.search))
            if parsed.store and stored is not None:
                path = store_work(stored, parsed.config)
                logger.info("stored: %s in %s", stored.url, path)
        runner.books()
    except (GetjmangaError, HTTPError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc

    logger.info("done.")


if __name__ == "__main__":
    main()
