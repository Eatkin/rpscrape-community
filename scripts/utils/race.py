import sys

from datetime import datetime
from jarowinkler import jarowinkler_similarity
from lxml import html
from lxml.html import HtmlElement
from orjson import loads
from re import search, sub
from typing import Any

from models.betfair import BSPMap
from models.race import RaceInfo, RunnerInfo

from utils.cleaning import clean_race, clean_string
from utils.date import convert_date
from utils.going import get_surface
from utils.lps import get_lps_scale
from utils.network import NetworkClient


regex_class = r"(\(|\s)(C|c)lass (\d|[A-Ha-h])(\)|\s)"
regex_group = r"(\(|\s)((G|g)rade|(G|g)roup) (\d|[A-Ca-c]|I*)(\)|\s)"
regex_rating_band = r"\((\d+-\d+)\)"

RACE_TYPE_CODES: dict[str, str] = {
    "F": "Flat",
    "H": "Hurdle",
    "C": "Chase",
}


class VoidRaceError(Exception):
    pass


def _id_from_url(url: str | None) -> str:
    if not url:
        return ""
    for segment in url.split("/"):
        if segment.isdigit():
            return segment
    return ""


def _clean_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"–", "-"} else text


class Race:
    def __init__(
        self,
        client: NetworkClient,
        url: str,
        document: HtmlElement,
        fields: list[str],
        bsp_map: BSPMap | None = None,
    ):
        self.url: str = url
        self.doc: HtmlElement = document
        self.race_info: RaceInfo = RaceInfo()
        self.runner_info: RunnerInfo = RunnerInfo()

        url_split = self.url.split("/")

        race_result = self._load_race_result(client)

        if not race_result["runners"]:
            raise VoidRaceError(f"VoidRaceError: {self.url}")

        header = race_result["header"]
        details = race_result["details"]

        self.race_info.course = header["courseDisplayName"]

        if "belmont at the big a" in self.race_info.course.lower():
            self.race_info.course_id = "255"
            self.race_info.course = "Aqueduct"
        else:
            self.race_info.course_id = url_split[4]

        # No equivalent of the old rp-raceTimeCourseName_distanceDetail DOM field (e.g.
        # straight/round course notes) was found in raceResult.data.
        self.race_info.course_detail = ""

        self.race_info.off = parse_time(race_result["raceDatetime"])
        self.race_info.date = convert_date(url_split[6])
        self.race_info.region = race_result.get("countryCode") or ""
        self.race_info.race_id = url_split[7]
        self.race_info.going = header.get("going") or ""
        self.race_info.surface = get_surface(self.race_info.going)
        self.race_info.race_name = clean_race(header["raceTitle"])
        self.race_info.race_class = f"Class {header['raceClass']}" if header.get("raceClass") else ""

        self.race_info.pattern = self.get_race_pattern()

        if self.race_info.race_class == "":
            self.race_info.race_class = self.get_race_class()

        self.race_info.age_band, self.race_info.rating_band = self.parse_race_bands(
            header["raceTitle"], header.get("agesAllowed")
        )
        self.race_info.sex_rest = self.sex_restricted()

        (
            self.race_info.dist,
            self.race_info.dist_y,
            self.race_info.dist_f,
            self.race_info.dist_m,
        ) = self.get_race_distances(header.get("distanceShort") or "", header.get("distanceYard"))

        self.race_info.race_type = RACE_TYPE_CODES.get(header.get("raceTypeCode") or "", "NH Flat")
        self.race_info.ran = str(details.get("numberOfRunners") or len(race_result["runners"]))

        self._parse_runners(race_result["runners"], header)

        self.runner_info.dec = self.get_decimal_odds()
        self.runner_info.time = self.get_finishing_times(details.get("winningTime"))
        self.runner_info.secs = self.time_to_seconds(self.runner_info.time)

        self.clean_non_completions()

        if bsp_map:
            self.join_betfair_data(bsp_map)

        self.csv_data: list[str] = self.create_csv_data(fields)

    def _load_race_result(self, client: NetworkClient) -> dict[str, Any]:
        doc = self.doc

        for _ in range(10):
            try:
                initial_state = loads(doc.get_element_by_id("__NEXT_DATA__").text_content())["props"]["pageProps"][
                    "initialState"
                ]
                race_result = initial_state["raceResult"]["data"]
            except KeyError:
                race_result = None

            if race_result is not None:
                self.doc = doc
                return race_result

            _, response = client.get(self.url)
            doc = html.fromstring(response.content)

        raise RuntimeError(f"Failed to load race result data for {self.url}")

    def _parse_runners(self, runners: list[dict[str, Any]], header: dict[str, Any]) -> None:
        prize_by_position = {
            str(p["position"]): p["formatted"].replace(",", "").replace("£", "") for p in header.get("prizes") or []
        }

        for runner in runners:
            pedigree = runner.get("pedigree") or {}
            comment = runner.get("comment") or {}

            self.runner_info.pos.append("DSQ" if runner.get("isDisqualified") else str(runner["outcomeCode"]))
            self.runner_info.num.append(str(runner.get("saddleClothNo") or ""))
            draw_label = runner.get("drawLabel")
            self.runner_info.draw.append(draw_label.strip("()") if draw_label else "")

            btn = distance_to_decimal(runner.get("beatenDistance") or "0")
            ovr_btn = distance_to_decimal(runner.get("beatenDistanceToWinner") or runner.get("beatenDistance") or "0")
            self.runner_info.btn.append(btn)
            self.runner_info.ovr_btn.append(ovr_btn)

            self.runner_info.horse_id.append(str(runner["horseUid"]))
            suffix = runner.get("horseSuffix") or "(GB)"
            self.runner_info.horse.append(f"{clean_string(runner['horseName'])} {suffix}")
            self.runner_info.age.append(str(runner.get("age") or ""))

            colour_sex = (pedigree.get("colourSex") or "").split()
            self.runner_info.sex.append(colour_sex[-1].upper() if colour_sex else "")

            stones, pounds = runner.get("weightStones"), runner.get("weightPounds")
            self.runner_info.wgt.append(f"{stones}-{pounds}" if stones is not None and pounds is not None else "")
            self.runner_info.lbs.append(str(runner.get("weightCarriedLbs") or ""))

            headgear = runner.get("headgear") or ""
            if runner.get("isFirstTimeHeadgear"):
                headgear += "1"
            self.runner_info.hg.append(headgear)

            self.runner_info.sp.append(_clean_value(runner.get("odds")))
            self.runner_info.jockey.append(clean_string(runner.get("jockeyName") or ""))
            self.runner_info.jockey_id.append(_id_from_url(runner.get("jockeyUrl")))
            self.runner_info.trainer.append(clean_string(runner.get("trainerName") or ""))
            self.runner_info.trainer_id.append(_id_from_url(runner.get("trainerUrl")))
            self.runner_info.owner.append(clean_string(runner.get("ownerName") or ""))
            self.runner_info.owner_id.append(_id_from_url(runner.get("ownerUrl")))

            prize = "" if runner.get("isDisqualified") else prize_by_position.get(str(runner["outcomeCode"]), "")
            self.runner_info.prize.append(prize)

            self.runner_info.ofr.append(_clean_value(runner.get("officialRating")))
            self.runner_info.rpr.append(_clean_value(runner.get("rpRating")))
            self.runner_info.ts.append(_clean_value(runner.get("topspeed")))

            self.runner_info.sire_id.append(_id_from_url(pedigree.get("sireUrl")))
            self.runner_info.sire.append(clean_string(pedigree.get("sireName") or ""))
            self.runner_info.dam_id.append(_id_from_url(pedigree.get("damUrl")))
            self.runner_info.dam.append(clean_string(pedigree.get("damName") or ""))
            self.runner_info.damsire_id.append(_id_from_url(pedigree.get("damSireUrl")))
            self.runner_info.damsire.append(clean_string(pedigree.get("damSireName") or ""))

            self.runner_info.silk_url.append(runner.get("silkUrl") or "")
            self.runner_info.comment.append(self.clean_comment(comment.get("comment") or ""))

    def calculate_times(self, win_time: float, dist_btn: list[str], going: str, race_type: str) -> list[str]:
        times: list[str] = []

        lps_scale = get_lps_scale(race_type, going)

        for dist in dist_btn:
            try:
                time = win_time + (float(dist) / lps_scale)
                minutes = int(time // 60)
                seconds = time % 60
                times.append(f"{minutes}:{seconds:05.2f}")
            except ValueError:
                times.append("")

        return times

    def clean_comment(self, comment: str) -> str:
        return comment.strip().replace("  ", "").replace(",", " -").replace("\n", " ").replace("\r", "")

    def clean_non_completions(self):
        for i, pos in enumerate(self.runner_info.pos):
            if not pos.isnumeric() and pos != "DSQ":
                self.runner_info.time[i] = "-"
                self.runner_info.secs[i] = "-"
                self.runner_info.ovr_btn[i] = "-"
                self.runner_info.btn[i] = "-"

    def create_csv_data(self, fields: list[str]) -> list[str]:
        field_mapping = {"type": "race_type", "class": "race_class", "or": "ofr"}

        race_values: list[str] = []
        runner_values: list[list[str]] = []

        for field in fields:
            actual_field = field_mapping.get(field, field)

            if hasattr(self.race_info, actual_field):
                race_values.append(str(getattr(self.race_info, actual_field)))
            elif hasattr(self.runner_info, actual_field):
                runner_values.append([str(v) for v in getattr(self.runner_info, actual_field)])

        race_prefix = ",".join(race_values)
        rows: list[str] = []
        for row in zip(*runner_values, strict=False):
            rows.append(race_prefix + "," + ",".join(row) if race_prefix else ",".join(row))
        return rows

    def get_decimal_odds(self):
        odds = [sub("(F|J|C)", "", sp) for sp in self.runner_info.sp]
        return fraction_to_decimal(odds)

    def get_finishing_times(self, winning_time_text: str | None):
        # adjust overall distance beaten when margins under a quarter length not accounted for
        # for instance when 2 horses finish with a head between them 4 lengths behind winner
        # both horses are recorded as being beaten 4 lengths overall by RP

        btn_adj: list[str] = []

        for btn, ovr_btn in zip(self.runner_info.btn, self.runner_info.ovr_btn):
            try:
                if float(ovr_btn) > 1 and float(btn) < 0.25:
                    btn_adj.append(str(float(btn) + float(ovr_btn)))
                else:
                    btn_adj.append(ovr_btn)
            except ValueError:
                btn_adj.append(ovr_btn)

        winning_time = self.parse_winning_time(winning_time_text)

        if winning_time is None:
            return ["-" for _ in range(int(self.race_info.ran))]

        return self.calculate_times(
            winning_time,
            btn_adj,
            self.race_info.going,
            self.race_info.race_type,
        )

    def get_race_class(self) -> str:
        classes = {
            "a": "1",
            "b": "2",
            "c": "3",
            "d": "4",
            "e": "5",
            "f": "6",
            "g": "6",
            "h": "7",
        }

        match = search(regex_class, self.race_info.race_name)

        if match:
            race_class = match.groups()[2].lower()
            if race_class in classes:
                return "Class " + classes[race_class]
            return "Class " + race_class

        if "(premier handicap)" in self.race_info.race_name:
            return "Class 2"

        if self.race_info.pattern:
            return "Class 1"

        return ""

    def get_race_distances(self, dist: str, dist_y: int | None) -> tuple[str, str, str, str]:
        try:
            dist_f = distance_to_furlongs(dist)
        except ValueError:
            print("ERROR: distance_to_furlongs()")
            print("Race: ", self.url)
            sys.exit()

        if dist_y:
            dist_m = round(dist_y / 1.0936)
        else:
            dist_m = round(dist_f * 201.168)
            dist_y = round(dist_m * 1.0936)

        dist_f_str = str(dist_f).replace(".0", "") + "f"

        if self.race_info.region not in {"GB", "IRE", "USA", "CAN"}:
            dist_m = round(float(dist_f_str.strip("f")) * 200)

        return dist, str(int(dist_y)), dist_f_str, str(int(dist_m))

    def get_race_pattern(self) -> str:
        match = search(regex_group, self.race_info.race_name)

        if match:
            pattern = f"{match.groups()[1]} {match.groups()[4]}".title()
            return pattern.title()

        if "Forte Mile" in self.race_info.race_name and "(Group" in self.race_info.race_name:
            return "Group 2"

        if any(x in self.race_info.race_name.lower() for x in {"listed race", "(listed"}):
            return "Listed"

        return ""

    def join_betfair_data(self, bsp_map: BSPMap):
        key = (self.race_info.region, self.race_info.date, self.race_info.off)
        bsp = bsp_map.get(key)

        self.runner_info.set_bsp_list_width(len(self.runner_info.horse))

        if not bsp:
            return

        for i, horse in enumerate(self.runner_info.horse):
            name = horse.split("(")[0].strip().lower()
            for row in bsp:
                if jarowinkler_similarity(name, row.horse) >= 0.77:
                    self.runner_info.bsp[i] = row.bsp or ""
                    self.runner_info.pre_min[i] = row.pre_min or ""
                    self.runner_info.pre_max[i] = row.pre_max or ""
                    self.runner_info.ip_min[i] = row.ip_min or ""
                    self.runner_info.ip_max[i] = row.ip_max or ""
                    self.runner_info.pre_vol[i] = row.pre_vol or ""
                    self.runner_info.ip_vol[i] = row.ip_vol or ""
                    break

    def parse_race_bands(self, race_title: str, ages_allowed: str | None) -> tuple[str, str]:
        age_band = (ages_allowed or "").strip()

        match = search(regex_rating_band, race_title)
        rating_band = match.group(1) if match else ""

        return age_band, rating_band

    def parse_winning_time(self, time_text: str | None) -> float | None:
        if not time_text:
            return None

        parts = time_text.strip().split()

        try:
            if len(parts) > 1:
                minutes = float(parts[0].rstrip("m"))
                seconds = float(parts[1].rstrip("s"))
                return round(minutes * 60 + seconds, 2)
            return round(float(parts[0].rstrip("s")), 2)
        except ValueError:
            return None

    def sex_restricted(self) -> str:
        race_name = self.race_info.race_name.lower()

        patterns = [
            (["entire colts & fillies", "colts & fillies"], "C & F"),
            (["fillies & mares", "filles & mares"], "F & M"),
            (["colts & geldings", "colts/geldings", "(c & g)"], "C & G"),
            (["(mares & geldings)"], "M & G"),
            (["fillies"], "F"),
            (["mares"], "M"),
        ]

        for terms, result in patterns:
            if any(term in race_name for term in terms):
                return result

        return ""

    def time_to_seconds(self, times: list[str]) -> list[str]:
        def convert_time(time_str: str) -> str:
            if time_str == "-":
                return "-"
            try:
                mins, secs = time_str.split(":")
                total_seconds = (int(mins) * 60) + float(secs)
                return f"{total_seconds:.2f}"
            except ValueError:
                raise ValueError(f"Invalid time format: '{time_str}' from {self.url}")

        return [convert_time(t) for t in times]


def distance_to_decimal(dist: str):
    return (
        dist.strip()
        .replace("¼", ".25")
        .replace("½", ".5")
        .replace("¾", ".75")
        .replace("lgnk", "0.4")
        .replace("snk", "0.2")
        .replace("nk", "0.3")
        .replace("sht-hd", "0.1")
        .replace("shd", "0.1")
        .replace("hd", "0.2")
        .replace("nse", "0.05")
        .replace("dht", "0")
        .replace("dist", "30")
    )


def distance_to_furlongs(distance: str):
    dist = "".join([d.strip().replace("¼", ".25").replace("½", ".5").replace("¾", ".75") for d in distance])

    if "m" in dist:
        if len(dist) > 2:
            dist = int(dist.split("m")[0]) * 8 + float(dist.split("m")[1].strip("f"))
        else:
            dist = int(dist.split("m")[0]) * 8
    else:
        dist = dist.strip("f")

    return float(dist)


def fraction_to_decimal(fractions: list[str]) -> list[str]:
    decimal: list[str] = []

    for fraction in fractions:
        if fraction in {"", "No Odds", "&"}:
            decimal.append("")
        elif fraction.lower() in {"evens", "evs"}:
            decimal.append("2.00")
        else:
            num, den = fraction.split("/")
            decimal.append(f"{float(num) / float(den) + 1.00:.2f}")

    return decimal


def parse_time(date_time: str):
    time = datetime.fromisoformat(date_time).time()
    return f"{time.strftime('%H:%M')}"
