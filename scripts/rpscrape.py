#!/usr/bin/env python3

import gzip
import shutil
import sys
from collections.abc import Callable
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from typing import TextIO

from lxml import html
from orjson import loads
from utils.argparser import ArgParser
from utils.betfair import Betfair
from utils.network import NetworkClient
from utils.paths import Paths
from utils.paths import build_paths
from utils.settings import Settings
from utils.update import Update

settings = Settings()

if TYPE_CHECKING:
    from utils.betfair import Betfair

RACE_TYPES: dict[str, set[str]] = {
    "flat": {"Flat"},
    "jumps": {"Chase", "Hurdle", "NH Flat"},
}


def check_for_update() -> bool:
    update = Update()

    if not update.available():
        return False

    choice = input("Update available. Do you want to update? [y/N] ").strip().lower()
    if choice != "y":
        return False

    success = update.pull_latest()

    if success:
        cache_root = Path(__file__).resolve().parents[1] / ".cache"

        if cache_root.exists():
            shutil.rmtree(cache_root)
        print("Updated successfully.")
    else:
        print("Failed to update.")

    return success


def clear_request(paths: Paths) -> None:
    for p in (
        paths.urls,
        paths.betfair,
        paths.progress,
        paths.output,
    ):
        if p.exists():
            p.unlink()


def sort_key(url: str) -> tuple[str, str]:
    parts = url.split("/")
    race_course = parts[5]
    race_date = parts[6]
    return race_date, race_course


def get_race_urls(years: list[str], tracks: list[tuple[str, str]], race_type: str, client: NetworkClient) -> list[str]:
    today = date.today()
    dates: list[date] = []

    for year in years:
        year_int = int(year)
        start = date(year_int, 1, 1)
        end = date(year_int, 12, 31) if year_int < today.year else today
        dates.extend(start + timedelta(days=i) for i in range((end - start).days + 1))

    return get_race_urls_date(dates, tracks, client)


def get_race_urls_date(dates: list[date], tracks: list[tuple[str, str]], client: NetworkClient) -> list[str]:
    urls: set[str] = set()
    course_ids: set[str] = {t[0] for t in tracks}

    for race_date in dates:
        url = f"https://www.racingpost.com/results/{race_date}"

        status, response = client.get(url)

        if status != 200:
            print(f"Failed to get results for {race_date} (status: {status})")
            continue

        doc = html.fromstring(response.content)

        try:
            initial_state = loads(doc.get_element_by_id("__NEXT_DATA__").text_content())["props"]["pageProps"][
                "initialState"
            ]
            courses = initial_state["results"]["data"] or []
        except KeyError:
            print(f"Failed to parse results listing for {race_date}")
            continue

        for course in courses:
            for race in course["races"]:
                if not race.get("fullResultAvailable") or not race.get("fullResultLink"):
                    continue
                if str(race["courseUid"]) in course_ids:
                    urls.add(f"https://www.racingpost.com{race['fullResultLink']}")

    return sorted(urls, key=sort_key)


def load_or_save_urls(
    path: Path,
    builder: Callable[[], list[str]],
) -> list[str]:
    if path.exists():
        return [line.strip() for line in path.read_text().splitlines() if line.strip()]

    urls = builder()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("\n".join(urls))

    return urls


def prepare_betfair(
    race_urls: list[str],
    paths: Paths,
) -> "Betfair | None":
    if not settings.toml or not settings.toml.get("betfair_data", False):
        return None

    from utils.betfair import Betfair

    if paths.betfair.exists():
        print("Using cached Betfair data")
        return Betfair.from_csv(paths.betfair)

    print("Fetching Betfair data...")

    betfair = Betfair(race_urls)

    with open(paths.betfair, "w") as f:
        fields = settings.toml.get("fields", {}).get("betfair", {})
        header = ",".join(["date", "region", "off", "horse", *list(fields.keys())])
        _ = f.write(header + "\n")

        for row in betfair.rows:
            values = ["" if v is None else str(v) for v in row.to_dict().values()]
            _ = f.write(",".join(values) + "\n")

    return betfair


def scrape_races(
    race_urls: list[str],
    paths: Paths,
    race_type: str,
    client: NetworkClient,
    file_writer: Callable[[str, bool], TextIO],
):
    from utils.race import Race
    from utils.race import VoidRaceError

    betfair = prepare_betfair(
        race_urls=race_urls,
        paths=paths,
    )

    last_url = paths.progress.read_text().strip() if paths.progress.exists() else None

    if last_url:
        try:
            race_urls = race_urls[race_urls.index(last_url) + 1 :]
            print(f"Resuming after {last_url}")
        except ValueError:
            pass
    else:
        print("Scraping races")

    append = last_url is not None and paths.output.exists()

    with file_writer(str(paths.output), append=append) as f:
        if not append:
            _ = f.write(settings.csv_header + "\n")

        for url in race_urls:
            _, response = client.get(url)
            doc = html.fromstring(response.content)

            try:
                race = (
                    Race(client, url, doc, settings.fields, betfair.data)
                    if betfair
                    else Race(client, url, doc, settings.fields)
                )
            except VoidRaceError:
                continue

            allowed = RACE_TYPES.get(race_type)
            if allowed is not None and race.race_info.race_type not in allowed:
                continue

            for row in race.csv_data:
                _ = f.write(row + "\n")

            _ = paths.progress.write_text(url)

    print("Finished scraping.")
    print(f"OUTPUT_CSV={paths.output.resolve()}")


def writer_csv(file_path: str, append: bool = False) -> TextIO:
    return open(file_path, "a" if append else "w", encoding="utf-8")


def writer_gzip(file_path: str, append: bool = False) -> TextIO:
    mode = "at" if append else "wt"
    return gzip.open(file_path, mode, encoding="utf-8")


def main():
    if settings.toml is None:
        sys.exit()

    if settings.toml["auto_update"]:
        _ = check_for_update()

    gzip_output = settings.toml.get("gzip_output", False)
    file_writer = writer_gzip if gzip_output else writer_csv

    parser = ArgParser()

    if len(sys.argv) <= 1:
        parser.parser.print_help()
        sys.exit(2)

    args = parser.parse(sys.argv[1:])
    paths = build_paths(args.request, gzip_output)

    if args.clean:
        clear_request(paths)

    client = NetworkClient()

    if args.dates != []:
        race_urls = load_or_save_urls(
            paths.urls,
            lambda: get_race_urls_date(args.dates, args.tracks, client),
        )

    else:
        race_urls = load_or_save_urls(
            paths.urls,
            lambda: get_race_urls(args.years, args.tracks, args.race_type, client),
        )

    scrape_races(race_urls, paths, args.race_type, client, file_writer)


if __name__ == "__main__":
    main()
