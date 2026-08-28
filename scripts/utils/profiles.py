import sys
from datetime import datetime
from typing import Any
from typing import NoReturn

from lxml import html
from orjson import loads
from utils.network import NetworkClient


def get_profiles(client: NetworkClient, horse_ids: list[int], urls: list[str]) -> dict[int, dict[str, Any]]:
    profiles: dict[int, dict[str, Any]] = {}

    for horse_id, url in zip(horse_ids, urls, strict=True):
        profiles[horse_id] = _extract_profile_from_url(client, url)

    return profiles


def _extract_profile_from_url(client: NetworkClient, url: str) -> dict[str, Any]:
    status, response = client.get(url)

    if status != 200:
        _exit_with_error(f"Failed to get profiles.\nStatus: {status}, URL: {url}")

    doc = html.fromstring(response.content)

    try:
        initial_state = loads(doc.get_element_by_id("__NEXT_DATA__").text_content())["props"]["pageProps"][
            "initialState"
        ]
        overview = initial_state["horseProfile"]["overview"]["data"]
        quotes = initial_state["horseProfile"]["quotes"]["data"] or []
    except KeyError:
        _exit_with_error(f'Failed to get profiles.\nNo "__NEXT_DATA__" data found at URL: {url}')

    dob, colour, sex = _parse_age_details(overview.get("ageDetails"))

    split = url.split("/")

    return {
        "profile": f"{split[5]}/{split[6]}",
        "sex": sex,
        "dob": dob,
        "colour": colour,
        # trainerStats is a formatted summary string ("(Last 14 days: 0-4, 0%)"), not the
        # structured dict the old trainerLast14Days field used to be.
        "trainer_14_days": overview.get("trainerStats"),
        "trainer_location": None,
        "breeder": overview.get("breederName"),
        "breeder_id": None,
        "previousTrainers": [
            {
                "trainerStyleName": change.get("newTrainer"),
                "trainerUid": None,
                "trainerChangeDate": change.get("sortDatetime"),
            }
            for change in overview.get("trainerChanges") or []
        ],
        "previousOwners": [
            {
                "ownerStyleName": owner.get("ownerStyleName"),
                "ownerUid": owner.get("ownerUid"),
                "ownerChangeDate": owner.get("ownerChangeDate"),
            }
            for owner in overview.get("previousOwners") or []
        ],
        # quotes were empty for every horse checked live while building this fix, so this
        # field mapping is a best-effort guess at the new shape and is unverified against
        # a horse that actually has quotes.
        "quotes": quotes,
        "stable_quotes": [],
        "medical": None,
    }


def _parse_age_details(age_details: str | None) -> tuple[str | None, str | None, str | None]:
    if not age_details:
        return None, None, None

    parts = age_details.split()
    if len(parts) < 3:
        return None, None, None

    dob_raw, colour, sex = parts[0], parts[1], parts[2]

    try:
        dob = datetime.strptime(dob_raw, "%d%b%y").date().isoformat()
    except ValueError:
        dob = None

    return dob, colour, sex


def _exit_with_error(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)
