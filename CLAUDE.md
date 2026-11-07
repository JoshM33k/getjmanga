# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed with [uv](https://docs.astral.sh/uv/) and every task is
defined in `mise.toml`, which is the canonical list.

```bash
uv sync --all-groups                   # install runtime + dev + docs groups
mise run pytest                        # run the test suite
uv run pytest tests/test_getjmanga.py::test_version_is_available  # a single test
uv run pytest -m "not network"          # skip the tests that hit the real sites
mise run ruff                          # format + autofix (uv format)
mise run ty                            # type check (uvx ty check)
mise run pymarkdown                    # markdown lint (top level and docs/)
mise run pyproject-fmt                 # normalize pyproject.toml
mise run pre-commit                    # ruff + ty + pymarkdown + pyproject-fmt
mise run ci                            # pre-commit + pytest-cov -- what CI runs
mise run build                         # build sdist + wheel
mise run docs                          # pdoc API docs into ./site
mise run pinup                         # update the pinned action/image digests
mise run build-binary                  # PyInstaller standalone binary into ./dist
```

The venv is tied to the absolute repo path (`uv sync` bakes it into script shebangs). If the
repo directory gets renamed or moved, delete `.venv/` and `uv sync` again rather than debugging
"No such file or directory" / `ModuleNotFoundError` -- it is a stale interpreter path, not a
code bug.

Lint config lives in `pyproject.toml`: Ruff with `lint.select = ["ALL"]` and `line-length = 120`.
Prefer a targeted `lint.per-file-ignores` entry with a comment over a scattered `# noqa`.

## Versioning and releases

Versions come from git tags via `uv-dynamic-versioning`; nothing in the repo hard-codes one.
Pushing a `v*.*.*` tag runs `build-binaries.yml`, which builds one binary per OS/arch on native
runners (PyInstaller cannot cross-compile), attaches them to a **draft** release and publishes it
afterwards -- immutable releases lock the assets of an already published release. `release.yml`
then reacts to `release: [published]` and does the PyPI and GHCR publish.

## Layout

- `getjmanga/extractors/<site>.py` -- one extractor per site (or per viewer
  many sites run unchanged, like GigaViewer and Comici+): the URL shapes,
  the listing, the titles, the login. An extractor imports `extractor`,
  `errors` and the packages below, never another extractor.
- `getjmanga/viewers/<viewer>.py` -- what several sites share because they
  run the same reader: SpeedBinb (`speedbinb`, also its static "PtBinb"
  export), YONDEMILL's reader on top of it (`yondemill`), PUBLUS (`publus`),
  the K MANGA viewer family (`kmanga`) and the `shuffle-seed` tile shuffle
  (`seedrandom`). Pure functions, plus -- where the viewer talks to a server
  the same way everywhere -- the requests, as functions taking the extractor
  whose session to use. Nothing in here is an `Extractor`.
- `getjmanga/extractor.py` -- the `Extractor` base class and what it hands
  back (`Episode`, `Page`); `getjmanga/errors.py` -- the exception hierarchy.
  Both sit above `extractors/` because the CLI, the downloader, the config
  and the viewers all speak in these terms.
- `getjmanga/console.py` -- what the CLI shows: `logger` for every
  message, and a `Display` for what is going on (the episode being read,
  the pages written), of which `setup()` picks one -- the compact live
  display (rich `Live`, one line per work once it is over), `-v` (one
  timestamped `logging` line per step, plus httpx2's per request) or `-q`
  (the base class: warnings and errors only). Nothing else in the package
  prints, apart from `jm config`'s answers.
- `getjmanga/cipher.py` -- the two ways page files are hidden in transit
  (AES-CBC, a repeating XOR key), undone.
- `getjmanga/kindle.py` -- `-K`: the chapters of a series on disk as `.cbz`
  books of ten chapters each (a `ComicInfo.xml` bookmark per chapter), for
  calibre to convert for a Kindle.
- `getjmanga/search.py` -- `-s`: the links on an arbitrary web page that some
  extractor's `suitable()` takes, for the CLI to download one by one.

## Testing conventions

Tests live in `tests/` and mirror the module split 1:1 (`tests/extractors/` for
the extractors, `tests/viewers/` for the viewers, `tests/test_extractor.py`,
`tests/test_cipher.py`).
`tests/conftest.py` provides the `fake_session`/`fake_response` fixtures -- a
an `httpx.Client` answering by substring match on the URL -- that every
extractor test scripts its site with. `tests/**` has its own
`lint.per-file-ignores` block, so assertions and missing annotations are fine there.

Each extractor test file ends with tests marked `@pytest.mark.network` that
download the first page of one free episode per known host from the real site,
from a module-level `TEST_URLS`; `test_registry.py::test_every_known_host_has_a_site_test`
reads every module's `TEST_URLS` and keeps their union equal to the `HOSTS` of
every extractor. CI runs them; locally, `-m "not network"` skips them. A
site that refuses GitHub's runners (403, 412, or a stand-in page) has its
network tests also marked `@pytest.mark.geoblocked`, which `conftest.py`
skips when `GITHUB_ACTIONS` is set and runs everywhere else.

Keep the offline tests to what the fake session can tell apart: one test per
observable outcome of `episode()` / `series_urls()` / `image()` / `login()`,
not per helper function, and nothing that re-checks the base class (`tests/test_extractor.py`), the
downloader, a shared viewer (tested in `tests/viewers/`), a cache, or a constant.
