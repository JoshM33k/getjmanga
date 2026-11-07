# getjmanga

[![PyPI](
  <https://img.shields.io/pypi/v/getjmanga?color=blue>
  )](
  <https://pypi.org/project/getjmanga/>
) [![CI](
  <https://github.com/eggplants/getjmanga/actions/workflows/ci.yml/badge.svg>
  )](
  <https://github.com/eggplants/getjmanga/actions/workflows/ci.yml>
)

**Android App 👉 [Godlo](https://egpl.dev/godlo/)✨**

Retrieve and save images from Japanese web comic sites.

_Do not redistribute the downloaded images. Keep them for private use._

## Supported sites

See [docs/SUPPORTED_SITES.md](
  <https://github.com/eggplants/getjmanga/blob/master/docs/SUPPORTED_SITES.md>
).

## Installation

```bash
# mise via github release
mise use -g github:eggplants/getjmanga

# mise via pipx
mise use -g pipx:getjmanga

# pipx
pipx install getjmanga

# pip
pip install getjmanga
```

## Docker

[![ghcr size](
  <https://ghcr-badge.egpl.dev/eggplants/getjmanga/size>
)](
  <https://github.com/eggplants/getjmanga/pkgs/container/getjmanga>
)

```bash
docker pull ghcr.io/eggplants/getjmanga

docker run --rm -v "$PWD:/work" -w /work \
  ghcr.io/eggplants/getjmanga https://takecomic.jp/episodes/74f33031e13cd
```

## CLI

```bash
# one episode
jm https://takecomic.jp/episodes/74f33031e13cd https://piccoma.com/web/viewer/8195/1185884

# episodes in bulk: this one and every next one
jm -b https://shonenjumpplus.com/episode/13932016480028799982

# every previous one too: the whole work from one episode
jm -B https://shonenjumpplus.com/episode/13932016480028799982

# login
jm -u you@example.com https://piccoma.com/web/viewer/8195/1185884

# save pages as png (or webp) instead of jpg
jm -F png https://takecomic.jp/episodes/74f33031e13cd

# also pack the saved pages into <series>/_cbz/<episode>.cbz, with a ComicInfo.xml naming the work and its author
jm -C https://takecomic.jp/episodes/74f33031e13cd

# once downloaded, put the chapters of the series saved so far into comic archives of ten chapters each,
# for calibre to convert for a Kindle: <series>/_kindle/<series> 001-010.cbz, 011-020.cbz, ...
# with a ComicInfo.xml bookmark per chapter; a book is rewritten when a chapter of it is new
jm -K https://www.viz.com/shonenjump/chapters/kagurabachi
# five chapters each instead
jm -K --per-book 5 https://www.viz.com/shonenjump/chapters/kagurabachi

# every link on a page that some extractor takes
jm -s https://shonenjumpplus.com/

# numbered pages
# 1 to 3
jm -s "https://comic-ryu.jp/series/list/up/[1-3]"
# from 1 on until a page has nothing to download
jm -s "https://comic-ryu.jp/series/list/up/[1-]"

# remember the work, then download what is new in every remembered work
jm -S -b https://shonenjumpplus.com/episode/13932016480028799982
jm patrol

# one timestamped log line per step and per request, instead of the live display
jm -v https://takecomic.jp/episodes/74f33031e13cd
# nothing but the warnings and the errors
jm -q https://takecomic.jp/episodes/74f33031e13cd
```

What is going on is shown on one or two lines that come down once a work is
done, leaving one line per work behind:

```text
saved: /home/you/manga/shonenjumpplus.com/阿波連さんははかれない (2 episodes, 1 skipped, 3 locked)
skipped: /home/you/manga/takecomic.jp/メイドインアビス (12 episodes)
done.
```

## Configuration

Use `jm c`.

Default: `~/.config/getjmanga/config.toml`

Example: [config.example.toml](
  <https://github.com/eggplants/getjmanga/blob/master/config.example.toml>
)

```bash
jm c init

# asks for the username and password
jm c site shonenjumpplus.com
jm c site piccoma

jm c savedir ~/manga
jm c overwrite true
jm c bulk false
jm c both true
jm c format webp
jm c cbz true
jm c kindle true
jm c metadata true

jm c patrol https://shonenjumpplus.com/episode/13932016480028799982
jm c patrol -s https://shonenjumpplus.com/

# read the file back: a key nothing reads, a savedir that is not a directory,
# a [site] no extractor reads, a patrol url no extractor takes or listed twice
jm c check
```

## Patrol

`jm -S` adds what it downloads to a list of works to watch for new episodes in the config file.

`jm c patrol <url>` adds a url to the list but does not download it.

`jm p` then goes through the list. It skips the episodes that are already saved, so it downloads only what is new.

What `jm p` does with an entry depends on what the entry is:

- For an episode, it follows the next links to the newest episode. The entry
  then moves to the first episode that is still locked. As a result, a
  wait-to-read episode gets one more try next time.
- For a series page, it reads the episode list again.
- For a page stored with `-s`, it scans the links again.

```toml
patrol = [
  { url = "https://shonenjumpplus.com/episode/13932016480028799982", title = "阿波連さんははかれない" },
  { url = "https://takecomic.jp/series/3f846451aff2d/1", title = "メイドインアビス" },
  { url = "https://shonenjumpplus.com/", search = true },
  ...
]
```

### Running every day

`jm p` saves into `savedir` from the config file, which defaults to the current directory,
so set it first: `jm c savedir ~/manga`. Both cron and systemd run with a minimal `PATH`,
so use the full path that `command -v jm` prints.

#### cron

`crontab -e`, then:

```crontab
# every day at 04:00, logging one line per work to a file
0 4 * * * /home/you/.local/bin/jm p >> /home/you/.local/state/getjmanga/patrol.log 2>&1

# log only the skips and the errors
0 4 * * * /home/you/.local/bin/jm p -q >> /home/you/.local/state/getjmanga/patrol.log 2>&1

# log every step, with a timestamp
0 4 * * * /home/you/.local/bin/jm p -v >> /home/you/.local/state/getjmanga/patrol.log 2>&1
```

#### systemd timer

`~/.config/systemd/user/getjmanga-patrol.service`:

```ini
[Unit]
Description=Download what is new in every patrolled work

[Service]
Type=oneshot
ExecStart=/home/you/.local/bin/jm p
```

`~/.config/systemd/user/getjmanga-patrol.timer`:

```ini
[Unit]
Description=Run getjmanga-patrol daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

`Persistent=true` runs the job at the next boot when the machine was off at the scheduled time.

```bash
systemctl --user daemon-reload
systemctl --user enable --now getjmanga-patrol.timer

# keep the user units running after logout
loginctl enable-linger "$USER"

# next run, and the output of the last one
systemctl --user list-timers getjmanga-patrol.timer
journalctl --user -u getjmanga-patrol.service
```

## Library

```python
from getjmanga import Downloader, find_extractor

url = "https://takecomic.jp/episodes/74f33031e13cd"
extractor = find_extractor(url)() # returns `Comici`
result = Downloader(extractor, "out", fmt="png", cbz=True).download(url)

result.status
result.save_dir

result.archive
result.episode.next_url
result.episode.writer
result.episode.publisher
result.episode.published
result.episode.number
```

An extractor on its own reads the site and writes nothing:

```python
from getjmanga import Comici

comici = Comici()
for url in comici.series_urls("https://takecomic.jp/series/b167ea507d35f"):
    episode = comici.episode(url)
    print(episode.episode_title, len(episode.pages), episode.readable)
```

## Writing an extractor

See [docs/ADD_SITE.md](
  <https://github.com/eggplants/getjmanga/blob/master/docs/ADD_SITE.md>
).

## License

[MIT License](
  <https://github.com/eggplants/getjmanga/blob/master/LICENSE.txt>
)
