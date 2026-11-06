"""The extractor registry: one class per site, or per viewer shared by many.

Order matters. `find_extractor()` hands a URL to the first class whose
`suitable()` accepts it, so a class that takes a broad set of URLs goes after
the ones that are picky.
"""

from __future__ import annotations

from getjmanga.errors import (
    GetjmangaError,
    LoginError,
    NotAnEpisodePageError,
    NothingReadableError,
    UnknownExtractorError,
    UnsupportedUrlError,
)
from getjmanga.extractor import Episode, Extractor, Page

from .alphapolis import AlphaPolis
from .beltoon import BeLToon
from .bloom import Bloom
from .boost import Boost
from .carula import Carula
from .ciao import Ciao
from .cmoa import Cmoa
from .comicessay import ComicEssay
from .comici import Comici
from .comicwalker import ComicWalker
from .corocoro import Corocoro
from .corona import Corona
from .crea import Crea
from .cycomi import Cycomi
from .daysneo import DaysNeo
from .drecomi import Drecomi
from .firecross import FireCross
from .fleur import Fleur
from .fullpercent import FullPercent
from .fuz import Fuz
from .gakcomic import Gakcomic
from .ganma import Ganma
from .gaugau import Gaugau
from .gecchan import Gecchan
from .gigaviewer import GigaViewer
from .ginkgo import Ginkgo
from .goraku import Goraku
from .hifumi import Hifumi
from .kirapo import Kirapo
from .laza import Laza
from .leedcafe import LeedCafe
from .lezhin import Lezhin
from .linemanga import LineManga
from .linku import FlowerComics, GanganOnline, MangaLab, MangaOne, MangaPark
from .magapoke import MagaPoke
from .manga5 import Manga5
from .mangabox import Mangabox
from .mangano import MangaNo
from .mavo import Mavo
from .mechacreators import MechaCreators
from .meets import Meets
from .michikusa import Michikusa
from .neetsha import Neetsha
from .nettai import Nettai
from .nicomanga import NicoManga
from .nora import Nora
from .ohta import Ohta
from .omocoro import Omocoro
from .pachikuri import Pachikuri
from .piccoma import Piccoma
from .pie import Pie
from .pixivcomic import PixivComic
from .porta import Porta
from .rookie import Rookie
from .saizensen import Saizensen
from .shiori import Shiori
from .shuro import Shuro
from .souffle import Souffle
from .splush import Splush
from .starts import Starts
from .sukupara import Sukupara
from .torch import Torch
from .vcomi import Vcomi
from .viz import Viz
from .wings import Wings
from .yanmaga import YanMaga
from .yawaspi import Yawaspi
from .ynjn import YanJan
from .yomonga import Yomonga
from .zerosum import ZeroSum

#: Every extractor, in the order URLs are matched against them.
EXTRACTORS: tuple[type[Extractor], ...] = (
    GigaViewer,
    Comici,
    Piccoma,
    Fuz,
    Mangabox,
    Gaugau,
    Souffle,
    Gecchan,
    Porta,
    Splush,
    ZeroSum,
    Ginkgo,
    Ohta,
    Nora,
    ComicWalker,
    ComicEssay,
    Fleur,
    Boost,
    MagaPoke,
    YanMaga,
    DaysNeo,
    Nettai,
    Cycomi,
    YanJan,
    Rookie,
    Meets,
    Pachikuri,
    Yawaspi,
    MangaOne,
    FlowerComics,
    GanganOnline,
    Ciao,
    Corocoro,
    Wings,
    Shiori,
    Corona,
    Mavo,
    MangaPark,
    MangaLab,
    Hifumi,
    Goraku,
    Pie,
    PixivComic,
    Drecomi,
    Vcomi,
    Starts,
    Lezhin,
    BeLToon,
    Manga5,
    Kirapo,
    Yomonga,
    Crea,
    Bloom,
    FireCross,
    Shuro,
    Laza,
    LeedCafe,
    Torch,
    AlphaPolis,
    NicoManga,
    MangaNo,
    MechaCreators,
    FullPercent,
    Ganma,
    LineManga,
    Saizensen,
    Sukupara,
    Omocoro,
    Neetsha,
    Michikusa,
    Carula,
    Gakcomic,
    Cmoa,
    Viz,
)


def find_extractor(url: str) -> type[Extractor]:
    """Pick the extractor that takes `url`.

    Args:
        url: The URL to download.

    Returns:
        The first class in `EXTRACTORS` whose `suitable()` accepts the URL.

    Raises:
        UnsupportedUrlError: No extractor takes the URL.
    """
    for extractor in EXTRACTORS:
        if extractor.suitable(url):
            return extractor
    msg = f"no extractor takes {url}; see --list-extractors for what is supported."
    raise UnsupportedUrlError(msg)


def get_extractor(name: str) -> type[Extractor]:
    """Look an extractor up by its `NAME`.

    Args:
        name: The name, as `--list-extractors` prints it.

    Returns:
        The class.

    Raises:
        UnknownExtractorError: No extractor goes by that name.
    """
    for extractor in EXTRACTORS:
        if name == extractor.NAME:
            return extractor
    names = ", ".join(extractor.NAME for extractor in EXTRACTORS)
    msg = f"no extractor is named {name!r}; pick one of: {names}."
    raise UnknownExtractorError(msg)


__all__ = (
    "EXTRACTORS",
    "AlphaPolis",
    "BeLToon",
    "Bloom",
    "Boost",
    "Carula",
    "Ciao",
    "Cmoa",
    "ComicEssay",
    "ComicWalker",
    "Comici",
    "Corocoro",
    "Corona",
    "Crea",
    "Cycomi",
    "DaysNeo",
    "Drecomi",
    "Episode",
    "Extractor",
    "FireCross",
    "Fleur",
    "FlowerComics",
    "FullPercent",
    "Fuz",
    "Gakcomic",
    "GanganOnline",
    "Ganma",
    "Gaugau",
    "Gecchan",
    "GetjmangaError",
    "GigaViewer",
    "Ginkgo",
    "Goraku",
    "Hifumi",
    "Kirapo",
    "Laza",
    "LeedCafe",
    "Lezhin",
    "LineManga",
    "LoginError",
    "MagaPoke",
    "Manga5",
    "MangaLab",
    "MangaNo",
    "MangaOne",
    "MangaPark",
    "Mangabox",
    "Mavo",
    "MechaCreators",
    "Meets",
    "Michikusa",
    "Neetsha",
    "Nettai",
    "NicoManga",
    "Nora",
    "NotAnEpisodePageError",
    "NothingReadableError",
    "Ohta",
    "Omocoro",
    "Pachikuri",
    "Page",
    "Piccoma",
    "Pie",
    "PixivComic",
    "Porta",
    "Rookie",
    "Saizensen",
    "Shiori",
    "Shuro",
    "Souffle",
    "Splush",
    "Starts",
    "Sukupara",
    "Torch",
    "UnknownExtractorError",
    "UnsupportedUrlError",
    "Vcomi",
    "Viz",
    "Wings",
    "YanJan",
    "YanMaga",
    "Yawaspi",
    "Yomonga",
    "ZeroSum",
    "find_extractor",
    "get_extractor",
)
