# rpscrape-community

#### Table of Contents

- [About This Fork](#about-this-fork)
- [Requirements](#requirements)
- [Install](#install)
- [Examples](#examples)
- [Scrape Racecards](#scrape-racecards)
- [Settings](#settings)
- [License](#license)

### About This Fork

`rpscrape-community` is a continuation of [rpscrape](https://github.com/joenano/rpscrape), originally created by **joenano**. The original repository has gone private/offline and is no longer maintained, and with changes to how Racing Post serves data, the scraping has broken. This fork exists to keep the scraper working against the current site and to carry the project forward.

The original repository had no license attached, so nothing here is retroactively relicensed — see [License](#license) for what that means in practice. If joenano would like this fork taken down, they can request that and it will be honoured.

### What's Changed

#### Authentication 

The original `rpscrape` relied on authentication for making requests, however the authentication used by Racing Post has changed, so this is no longer viable without further investigation.

#### Scraping Method

Previously Racing Post's public data API was used, but this has since be deprecated with json data being embedded into the html pages themselves.

#### Data Collection

Some data that was previously available via racecards is now gated behind a horse's profile - this can be enabled in `settings.toml` by setting `fetch_profiles = true`

Race results are streamed to the output CSV as each race is scraped, rather than being collected in memory and dumped at the end - so you can `tail -f` the output file to watch results land, and progress is preserved incrementally if a run is interrupted (see the caching note under [Command-Line Options](#command-line-options)).

#### Formatting

Set up formatting and linting rules in `pyproject.toml` using `ruff` and `ty`.

#### Rate Limiting

In my experience Racing Post _will_ IP block you if you scrape too aggressively, and unfortunately implementing rate limiting is the only way to respect this, so scraping will be significantly slower than what was previously possible.

Rate limiting is configurable via the `[network]` table in `settings.toml`:

```toml
[network]
timeout = 14        # Request timeout in seconds
min_interval = 2.0  # Minimum seconds to wait between requests
jitter = 1.0        # Random extra delay (0-jitter seconds) added on top of min_interval
retries = 7         # Number of attempts before giving up on a blocked (406) request
retry_delay = 1.4   # Base delay in seconds between retries, doubles after each attempt
```

Lower `min_interval`/`jitter` to scrape faster at greater risk of being blocked, or raise them to be more conservative. As with other settings, copy `default_settings.toml` to `user_settings.toml` to override these without your changes being clobbered on update - see [Settings](#settings).

### Requirements

You must have Python 3.13 or greater, and GIT installed. You can download the latest Python release [here](https://www.python.org/downloads/). You can download GIT [here](https://git-scm.com/downloads).

- [curl_cffi](https://pypi.org/project/curl-cffi/)
- [jarowinkler](https://pypi.org/project/jarowinkler/)
- [LXML](https://lxml.de/)
- [orjson](https://pypi.org/project/orjson/1.3.0/)
- [tomli](https://pypi.org/project/tomli/)
- [TQDM](https://pypi.org/project/tqdm/)

The above Python modules are required, they can be installed using PIP(_included with Python_):

```
pip3 install curl_cffi jarowinkler lxml orjson tomli tqdm
```

### Install

```
git clone <this repository's URL>
```

#### Command-Line Options

```
-d, --date      Single date or date range YYYY/MM/DD-YYYY/MM/DD.
-y, --year      Year or year range (YYYY or YYYY-YYYY).
-r, --region    Region code (e.g., gb, ire).
-c, --course    Numeric course code.
-t, --type      Race type: flat or jumps.

--date-file     File containing dates (one per line, YYYY/MM/DD).

--clean         Clear cache and data before running request.

--regions       List or search regions.
--courses       List/search courses or list courses in a region.
```

##### Notes

--date and --year are mutually exclusive.

You cannot specify both --region and --course at the same time.

When scraping jumps data, the year refers to the season start. For example, the 2019 Cheltenham Festival is in the 2018-2019 season: use 2018.

Each request (a given date/year + region/course + type combination) caches its race URL list and scraping progress under `.cache/`, so re-running the same request resumes where it left off instead of re-fetching everything. If a request looks stuck or produces no output - for example after an interrupted run, or if Racing Post's listing changed - delete that request's files under `.cache/` (or just run with `--clean`) to force it to start fresh.

### Examples

##### All scripts should be run from the scripts directory.

All races on a specific date:

```
cd scripts
./rpscrape.py -d 2020/10/01
```

Only races from Great Britain:

```
./rpscrape.py -d 2020/10/01 -r gb
```

Date range:

```
./rpscrape.py -d 2019/12/15-2019/12/18
```

Flat races in Ireland (2019):

```
./rpscrape.py -r ire -y 2019 -t flat
```

Jump races at Ascot (1999–2018):

```
./rpscrape.py -c 2 -y 1999-2018 -t jumps
```

##### Date File Mode

Scrape using a file with dates:

```
./rpscrape.py --date-file dates.txt
```

one date per line, format: YYYY/MM/DD.

```
2020/10/01
2020/11/02
2020/12/03
```

##### Searching

List all regions:

```
./rpscrape.py --regions
```

Search regions:

```
./rpscrape.py --regions gb
```

List all courses:

```
./rpscrape.py --courses
```

Search courses:

```
./rpscrape.py --courses Ascot
```

List courses in a region:

```
./rpscrape.py --courses gb
```

##### Settings

The `user_settings.toml` file contains the data fields that can be scraped. You can turn fields on and off by setting them true or false. The order of fields in that file will be maintained in the output csv. The `default_settings.toml` file should not be edited, its there as a backup and to introduce any new fields without changing user settings.

The same file's `[network]` table controls rate limiting - see [Rate Limiting](#rate-limiting).

## Scrape Racecards

You can scrape racecards using racecards.py which saves a file containing a json object of racecard information.

There are only three parameter options, --day N, --days N where N is a number 1-2, and --region N where N is a region (gb, ire, etc).

##### Examples

Scrape today's racecards.

```
./racecards.py --day 1
```

Scrape tomorrow's racecards.

```
./racecards.py --day 2
```

Scrape today's and tomorrow's racecards.

```
./racecards.py --days 2
```

Scrape today's and tomorrow's racecards by region.

```
./racecards.py --days 2 --region gb
```

##### Settings

You can customize which data is included in racecards using the settings file. The scraper uses `settings/user_racecard_settings.toml` if it exists, otherwise falls back to `settings/default_racecard_settings.toml`.

To customize:

1. Copy `default_racecard_settings.toml` to `user_racecard_settings.toml`
2. Edit the settings to enable/disable field groups and data collection options

The settings file lets you control:

- **Data Collection**: Whether to fetch stats and profiles
- **Field Groups**: Which groups of runner fields to include (core, basic_info, performance, jockey, trainer, etc.)

### License

No license was ever attached to the original [joenano/rpscrape](https://github.com/joenano/rpscrape) repository, so none of that original code can be retroactively relicensed by this fork — it remains all-rights-reserved to joenano by default, and stays that way here too.

Changes made in this fork **from the point it diverged onward** are released under the MIT License (see [LICENSE](LICENSE)). This is a practical, forward-looking choice, not a claim of rights over joenano's original work.

If joenano (or whoever holds rights to the original work) objects to this fork existing, or wants it taken down, that request will be honoured.
