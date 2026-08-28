#!/usr/bin/env python

import argparse
import datetime
import os
import sys
from collections import defaultdict
from functools import partial
from pathlib import Path
from typing import Any

import tomli
from lxml import html
from models.racecard import Racecard
from models.racecard import Runner
from orjson import dumps
from orjson import loads
from tqdm import tqdm
from utils.cleaning import clean_string
from utils.network import NetworkClient
from utils.profiles import get_profiles
from utils.region import valid_region
from utils.stats import Stats

type Racecards = defaultdict[str, defaultdict[str, defaultdict[str, dict[str, Any]]]]


def load_field_config() -> dict[str, Any]:
    """Load field configuration from settings/user_racecard_settings.toml or default_racecard_settings.toml"""
    user_config_path = Path("../settings/user_racecard_settings.toml")
    default_config_path = Path("../settings/default_racecard_settings.toml")

    # Try user config first, fallback to default
    config_path = user_config_path if user_config_path.exists() else default_config_path

    if not config_path.exists():
        # Return default config (everything enabled)
        return {
            "data_collection": {"fetch_profiles": False, "fetch_stats": False, "max_days": 2},
            "field_groups": {},  # Empty means all groups enabled
        }

    with open(config_path, "rb") as f:
        config = tomli.load(f)

    return config


def validate_days_range(value: str, max_days: int) -> int:
    try:
        days = int(value)
        if 1 <= days <= max_days:
            return days
        raise argparse.ArgumentTypeError(f"Value must be an integer between 1 and {max_days}. Got: {days}")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid value: '{value}'. Expected an integer.") from e


def get_meetings(client: NetworkClient, dates: list[str], region: str | None = None) -> dict[str, list[dict[str, Any]]]:
    meetings: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    url = "https://www.racingpost.com/api/racing/meetings/?date="

    for date in dates:
        status, response = client.get(url + date)

        if status != 200:
            print(f"Failed to get racecards for {date} (status: {status})")
            continue

        for meeting in response.json()["meetings"]:
            if region and meeting["venueCountryCode"].lower() != region.lower():
                continue

            meetings[date].append(meeting)

    return dict(meetings)


def _horse_id_from_url(url: str | None) -> int | None:
    if not url:
        return None
    for segment in url.split("/"):
        if segment.isdigit():
            return int(segment)
    return None


def parse_runners(
    stats: Stats | None,
    runners_json: list[dict[str, Any]],
    profiles: dict[int, dict[str, Any]],
    config: dict[str, Any],
) -> list[Runner]:
    runners: list[Runner] = []
    field_groups = config.get("field_groups", {})
    data_opts = config.get("data_collection", {})

    # If field_groups is empty, include all groups
    include_all_groups = not field_groups

    # Helper to check if a group should be included
    def should_include_group(group: str) -> bool:
        if include_all_groups:
            return True
        if group not in field_groups:
            raise KeyError(f"Unknown field group: {group}")
        return field_groups[group] is True

    for runner_json in runners_json:
        profile = None
        if data_opts.get("fetch_profiles", True):
            try:
                profile = profiles[runner_json["horseId"]]
            except KeyError:
                print(f"Failed to find profile: {runner_json['horseId']} - {runner_json['horseName']}")
                print(dumps(runner_json).decode("utf-8"))
                sys.exit(1)

        runner = Runner()
        runners.append(runner)

        # Core identification fields
        if should_include_group("core"):
            runner.name = clean_string(runner_json["horseName"])
            runner.horse_id = runner_json["horseId"]
            runner.number = runner_json["startNumber"]
            runner.draw = runner_json["draw"] if runner_json["draw"] else None

        # Basic info
        # colour, dob and sex_code are no longer present on the racecard page's __NEXT_DATA__
        # runner blob at all; they're only recoverable via an opt-in horse profile fetch.
        if should_include_group("basic_info"):
            runner.age = runner_json["age"]
            runner.region = runner_json["countryOrigin"]
            if profile:
                runner.sex = profile.get("sex")
                runner.sex_code = profile.get("sex")
                runner.dob = profile.get("dob")
                runner.colour = profile.get("colour")

        # Performance
        if should_include_group("performance"):
            figures = sorted(runner_json["formFiguresData"], key=lambda f: f["position"])
            runner.form = "".join(f["figure"] for f in figures) if figures else ""
            runner.rpr = runner_json["rpPostmark"] if runner_json["rpPostmark"] else None
            runner.ts = runner_json["rpTopspeed"] if runner_json["rpTopspeed"] else None
            runner.ofr = runner_json["officialRatingToday"] if runner_json["officialRatingToday"] else None
            runner.last_run = runner_json["daysSinceLastRun"]

        # Jockey fields
        if should_include_group("jockey"):
            runner.jockey = clean_string(runner_json["jockeyName"])
            runner.jockey_id = runner_json["jockeyId"]
            runner.jockey_allowance = runner_json["weightAllowanceLbs"]
            runner.claim = runner_json["weightAllowanceLbs"]

        # Trainer fields
        if should_include_group("trainer"):
            runner.trainer = clean_string(runner_json["trainerName"])
            runner.trainer_id = runner_json["trainerId"]
            runner.trainer_rtf = runner_json["trainerRtf"]
            if profile:
                runner.trainer_location = profile.get("trainer_location")
                runner.trainer_14_days = profile.get("trainer_14_days")

        # Weight
        if should_include_group("weight"):
            runner.lbs = runner_json["weightCarried"]

        # Equipment
        if should_include_group("equipment"):
            runner.headgear = runner_json["horseHeadGear"]
            runner.headgear_first = runner_json["horseHeadGearFirstTime"]
            runner.gelding_first_time = runner_json["geldingFirstTime"]
            runner.wind_surgery_first = bool(runner_json["windSurgery"])
            runner.wind_surgery_second = False

        # Breeding
        if should_include_group("breeding"):
            runner.sire = clean_string(runner_json["sireName"])
            runner.sire_id = _horse_id_from_url(runner_json.get("sireUrl"))
            runner.sire_region = runner_json["sireCountry"]
            runner.dam = clean_string(runner_json["damName"])
            runner.dam_id = _horse_id_from_url(runner_json.get("damUrl"))
            runner.dam_region = runner_json["damCountry"]
            runner.damsire = clean_string(runner_json["damsireName"])
            runner.damsire_id = _horse_id_from_url(runner_json.get("damsireUrl"))
            runner.damsire_region = runner_json["damsireCountry"]
            if profile:
                runner.breeder = profile.get("breeder")
                runner.breeder_id = profile.get("breeder_id")

        # Ownership
        if should_include_group("ownership"):
            runner.owner = clean_string(runner_json["ownerName"])
            runner.owner_id = runner_json["ownerId"]

        # Comments
        if should_include_group("comments"):
            runner.comment = runner_json["diomed"]
            runner.spotlight = runner_json["spotlight"]

        # Status
        if should_include_group("status"):
            runner.non_runner = runner_json["nonRunner"]
            runner.reserve = runner_json["irishReserve"]

        # Silk
        if should_include_group("silk"):
            silk_image = runner_json.get("silkImage")
            runner.silk_url = silk_image
            runner.silk_path = (
                silk_image.removeprefix("https://www.rp-assets.com/svg/").removesuffix(".svg") if silk_image else None
            )

        # Profile data
        if should_include_group("profile") and profile:
            runner.profile = profile.get("profile")

        # Stats data
        if should_include_group("stats") and stats:
            horse_stats = stats.horses[str(runner.horse_id)].to_dict() if str(runner.horse_id) in stats.horses else {}
            jockey_stats = stats.jockeys.get(str(runner.jockey_id), {})
            trainer_stats = stats.trainers.get(str(runner.trainer_id), {})
            runner.stats = {
                "horse": horse_stats,
                "jockey": jockey_stats,
                "trainer": trainer_stats,
            }

        # History data
        if should_include_group("history") and profile:
            if profile.get("previousTrainers"):
                runner.prev_trainers = [
                    {
                        "trainer": clean_string(trainer["trainerStyleName"]),
                        "trainer_id": trainer["trainerUid"],
                        "change_date": trainer["trainerChangeDate"].split("T")[0],
                    }
                    for trainer in profile["previousTrainers"]
                ]
            if profile.get("previousOwners"):
                runner.prev_owners = [
                    {
                        "owner": clean_string(owner["ownerStyleName"]),
                        "owner_id": owner["ownerUid"],
                        "change_date": owner["ownerChangeDate"].split("T")[0],
                    }
                    for owner in profile["previousOwners"]
                ]

        # Medical data
        if should_include_group("medical") and profile and profile.get("medical"):
            runner.medical = [
                {"date": med["medicalDate"].split("T")[0], "type": med["medicalType"]} for med in profile["medical"]
            ]

        # Quotes data
        if should_include_group("quotes") and profile:
            if profile.get("quotes"):
                runner.quotes = [
                    {
                        "date": (q.get("raceDate") or "").split("T")[0] or None,
                        "horse": clean_string(q["horseStyleName"]) if q.get("horseStyleName") else None,
                        "horse_id": q.get("horseUid"),
                        "race": q.get("raceTitle"),
                        "race_id": q.get("raceId"),
                        "course": q.get("courseStyleName"),
                        "course_id": q.get("courseUid"),
                        "distance_f": q.get("distanceFurlong"),
                        "distance_y": q.get("distanceYard"),
                        "quote": q.get("notes"),
                    }
                    for q in profile["quotes"]
                ]
            if profile.get("stable_quotes"):
                runner.stable_tour = [
                    {
                        "horse": clean_string(q["horseName"]),
                        "horse_id": q["horseUid"],
                        "quote": q["notes"],
                    }
                    for q in profile["stable_quotes"]
                ]

    return runners


def scrape_racecards(
    meetings: list[dict[str, Any]],
    date: str,
    config: dict[str, Any],
    client: NetworkClient,
) -> Racecards:
    racecards: Racecards = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

    data_opts: dict[str, Any] = config.get("data_collection", {})
    fetch_profiles: bool = data_opts.get("fetch_profiles", False)
    fetch_stats: bool = data_opts.get("fetch_stats", False)

    for meeting in tqdm(
        meetings,
        desc=date,
        bar_format="{desc}: {percentage:3.0f}% |{bar:49}| {n}/{total} ETA {remaining}",
        ncols=91,
    ):
        course_id = meeting["venueUid"]
        course_key = meeting["courseKey"]

        for race in meeting["races"]:
            race_id = race["raceId"]

            url_base = "https://www.racingpost.com"
            url_racecard = f"{url_base}/racecards/{course_id}/{course_key}/{date}/{race_id}/"

            status_racecard, resp_racecard = client.get(url_racecard)

            if status_racecard != 200:
                print("Failed to get racecard data.")
                print(f"status: {status_racecard} url: {url_racecard}")
                continue

            doc = html.fromstring(resp_racecard.content)
            json_string = doc.get_element_by_id("__NEXT_DATA__").text_content()

            try:
                data = loads(json_string)["props"]["pageProps"]["initialState"]
                meeting_meta = data["meetings"]["byDate"][date]["races"]["byRaceId"][race_id]
                race_meta = data["racePage"]["data"]["race"]
                runners = data["racePage"]["data"]["runners"]
            except KeyError:
                print("Failed to get racecard data.")
                print(f"Invalid JSON at URL: {url_racecard}")
                continue

            profiles: dict[int, dict[str, Any]] = {}
            if fetch_profiles:
                horse_ids = [r["horseId"] for r in runners]
                profile_urls = [f"https://www.racingpost.com{r['horseUrl'].split('#')[0]}" for r in runners]
                profiles = get_profiles(client, horse_ids, profile_urls)

            stats = None
            if fetch_stats:
                status, resp = client.get(
                    f"https://www.racingpost.com/api/racing/free-stats-tab/?raceId={race_id}&date={date}"
                )
                if status == 200:
                    stats = Stats(resp.json())

            racecard: Racecard = Racecard()

            racecard.href = url_racecard
            racecard.race_id = int(race_id)
            racecard.date = date

            racecard.off_time = race_meta["startTime"]

            racecard.course_id = course_id
            racecard.course = race_meta["courseStyleName"]

            racecard.course_detail = race_meta["straightRoundJubileeCode"]
            racecard.course_info = data["racePage"]["data"]["courseInfo"]

            if racecard.course == "Belmont At The Big A":
                racecard.course_id = 255
                racecard.course = "Aqueduct"

            racecard.region = meeting["venueCountryCode"]

            racecard.race_name = race["raceTitle"]
            racecard.race_type = race["raceType"]

            racecard.distance_f = race_meta["distanceFurlongs"]
            racecard.distance_y = race_meta["distanceYards"]
            racecard.distance = meeting_meta["displayDistance"]

            racecard.pattern = race_meta["raceGroupDesc"]
            racecard.race_class = race["raceClass"]
            racecard.age_band = race["ageRestriction"]
            racecard.rating_band = race["ratingBand"]

            racecard.prizes = [{str(x["position_no"]): x["prize_sterling"]} for x in race_meta["prizes"]]
            racecard.prize = race_meta["totalPrizeMoney"]["total_prize_sterling"]
            racecard.prize_winner = race_meta["formattedTotalPrizeMoney"]

            racecard.field_size = race["numberOfRunners"]

            racecard.handicap = race["isHandicap"]
            racecard.going = race["going"]
            racecard.surface = race["surfaceType"]
            racecard.category = race["category"]

            racecard.runners = parse_runners(stats, runners, profiles, config)

            assert racecard.region is not None
            assert racecard.course is not None
            assert racecard.off_time is not None

            racecards[racecard.region][racecard.course][racecard.off_time] = racecard.to_dict()

    return racecards


def main() -> None:
    config = load_field_config()
    max_days = config.get("data_collection", {}).get("max_days", 2)

    parser = argparse.ArgumentParser(
        description="Scrape racecards for a single day or a range of days.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    flag_group = parser.add_mutually_exclusive_group()

    validate_with_limit = partial(validate_days_range, max_days=max_days)

    _ = flag_group.add_argument(
        "--day",
        type=validate_with_limit,
        help="Scrape a single specific day (N).",
        metavar="N",
    )

    _ = flag_group.add_argument(
        "--days",
        type=validate_with_limit,
        help="Scrape a range of days (N total).",
        metavar="N",
    )

    _ = parser.add_argument(
        "--region",
        type=str,
        help="Region code to filter by (e.g., 'gb', 'ire').",
        metavar="CODE",
    )

    args = parser.parse_args()

    dates: list[str] = [(datetime.date.today() + datetime.timedelta(days=i)).isoformat() for i in range(max_days)]

    if args.day:
        dates = [dates[args.day - 1]]
    elif args.days:
        dates = dates[: args.days]
    else:
        parser.print_usage(sys.stderr)
        print(f"\nError: Must specify a day (--day) or days (--days) (1-{max_days})")
        sys.exit(1)

    if not os.path.exists("../racecards"):
        os.makedirs("../racecards")

    region = args.region.lower() if args.region else None

    if region and not valid_region(region):
        print(f"Invalid region: {args.region}")
        sys.exit(1)

    client = NetworkClient()

    meetings = get_meetings(client, dates, region)

    for date in meetings:
        racecards = scrape_racecards(meetings[date], date, config, client)

        with open(f"../racecards/{date}.json", "w", encoding="utf-8") as f:
            _ = f.write(dumps(racecards).decode("utf-8"))


if __name__ == "__main__":
    main()
