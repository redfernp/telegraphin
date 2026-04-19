"""
Telegraph India Horse Racing Tip Scraper.

Hub:  https://www.telegraphindia.com/sports/horse-racing/
Meeting pages list one racecard URL per race.
For each race pick the youngest of the top-3 tipped horses; tie-break on
highest Night Odds, then lowest race number. Across a meeting, the
pick with the lowest night odds becomes NAP, second lowest becomes NB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from bs4 import BeautifulSoup
from curl_cffi import requests

HUB_URL = "https://www.telegraphindia.com/sports/horse-racing/"
IMPERSONATE = "chrome131"


@dataclass
class Horse:
    number: int
    name: str
    age: int | None
    night_odds: Fraction | None
    night_odds_raw: str


@dataclass
class RacePick:
    race_number: int
    race_url: str
    horse: Horse
    tips: list[int]


@dataclass
class Meeting:
    name: str
    url: str
    picks: list[RacePick]


# ---------- HTTP ----------

def _fetch(url: str, referer: str | None = None) -> str:
    headers = {"Referer": referer} if referer else None
    r = requests.get(url, headers=headers, impersonate=IMPERSONATE, timeout=30)
    r.raise_for_status()
    return r.text


# ---------- Hub / meeting discovery ----------

_MEETING_HREF_RE = re.compile(r"/race-calendar/[^/]+-\d+$")


def get_upcoming_meetings() -> list[tuple[str, str]]:
    """Return [(name, url)] for meetings that are Due or Live on the hub page."""
    soup = BeautifulSoup(_fetch(HUB_URL), "lxml")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _MEETING_HREF_RE.search(href):
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        status = next((s for s in ("Due", "Live", "Over") if f" {s}" in f" {text}"), None)
        if status not in ("Due", "Live"):
            continue
        if href in seen:
            continue
        seen.add(href)
        name = text.split(f" {status}")[0].strip()
        found.append((name, href))
    return found


def get_race_urls(meeting_url: str) -> list[str]:
    """Return racecard URLs in order for the given meeting."""
    soup = BeautifulSoup(_fetch(meeting_url, referer=HUB_URL), "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/racecard-" not in href or href in seen:
            continue
        seen.add(href)
        urls.append(href)
    # Sort by the race number embedded in the URL (racecard-N-ID)
    def key(u: str) -> int:
        m = re.search(r"/racecard-(\d+)-", u)
        return int(m.group(1)) if m else 0
    urls.sort(key=key)
    return urls


# ---------- Race card parsing ----------

_AGE_RE = re.compile(r"\[[^\]]*?(\d+)\s*\]")
_FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _parse_odds(raw: str) -> Fraction | None:
    m = _FRAC_RE.search(raw)
    if not m:
        return None
    num, den = int(m.group(1)), int(m.group(2))
    if den == 0:
        return None
    return Fraction(num, den)


def parse_race(race_url: str, referer: str | None = None) -> tuple[list[int], list[Horse]]:
    soup = BeautifulSoup(_fetch(race_url, referer=referer), "lxml")

    tips: list[int] = []
    tips_block = soup.find(class_="asi-tips")
    if tips_block:
        for li in tips_block.find_all("li"):
            txt = li.get_text(strip=True)
            if txt.isdigit():
                tips.append(int(txt))

    horses: list[Horse] = []
    for item in soup.select(".bxlistitemrace .listitem"):
        num_el = item.select_one(".no-title-topbets")
        name_el = item.select_one(".horse-name strong")
        if not num_el or not name_el:
            continue
        num_txt = num_el.get_text(strip=True)
        if not num_txt.isdigit():
            continue
        number = int(num_txt)
        name = name_el.get_text(" ", strip=True)

        # Age from the [b m 5] code that sits in a sibling span
        hnbgn = item.select_one(".horse-name .hnbgn") or item.select_one(".horse-name")
        age = None
        if hnbgn:
            m = _AGE_RE.search(hnbgn.get_text(" ", strip=True))
            if m:
                age = int(m.group(1))

        # Night odds live in .horse-details li > strong
        night_raw = ""
        night_odds: Fraction | None = None
        details = item.select_one(".horse-details")
        if details:
            for li in details.find_all("li"):
                strong = li.find("strong")
                if strong and "night odds" in strong.get_text(strip=True).lower():
                    night_raw = strong.get_text(" ", strip=True)
                    night_odds = _parse_odds(night_raw)
                    break

        horses.append(Horse(number, name, age, night_odds, night_raw))
    return tips, horses


# ---------- Selection logic ----------

def pick_for_race(tips: list[int], horses: list[Horse]) -> Horse | None:
    """Youngest of the top-3 tipped; tiebreak highest night odds, then lowest number."""
    by_num = {h.number: h for h in horses}
    tipped = [by_num[n] for n in tips[:3] if n in by_num]
    if not tipped:
        return None

    ages = [h.age for h in tipped if h.age is not None]
    if ages:
        youngest = min(ages)
        candidates = [h for h in tipped if h.age == youngest]
    else:
        candidates = list(tipped)

    if len(candidates) == 1:
        return candidates[0]

    # Tie-break on night odds: highest wins; if only one has odds, pick that
    with_odds = [h for h in candidates if h.night_odds is not None]
    if len(with_odds) == 1:
        return with_odds[0]
    if with_odds:
        max_odds = max(h.night_odds for h in with_odds)
        top = [h for h in with_odds if h.night_odds == max_odds]
        if len(top) == 1:
            return top[0]
        candidates = top  # fall through to race-number tiebreak

    return min(candidates, key=lambda h: h.number)


def scrape_meeting(meeting_name: str, meeting_url: str) -> Meeting:
    race_urls = get_race_urls(meeting_url)
    picks: list[RacePick] = []
    for i, url in enumerate(race_urls, start=1):
        tips, horses = parse_race(url, referer=meeting_url)
        horse = pick_for_race(tips, horses)
        if horse is None:
            continue
        picks.append(RacePick(race_number=i, race_url=url, horse=horse, tips=tips))
    return Meeting(name=meeting_name, url=meeting_url, picks=picks)


# ---------- Output ----------

def _meeting_display_name(raw: str) -> str:
    """'Delhi Races' -> 'DELHI', 'Calcutta Races' -> 'CALCUTTA'."""
    first = raw.split()[0] if raw else raw
    return first.upper()


def format_output(meetings: list[Meeting]) -> str:
    lines: list[str] = []
    for meeting in meetings:
        if not meeting.picks:
            continue
        lines.append(_meeting_display_name(meeting.name))

        ranked = sorted(
            meeting.picks,
            key=lambda p: (
                p.horse.night_odds is None,
                p.horse.night_odds if p.horse.night_odds is not None else Fraction(10**9),
                p.race_number,
            ),
        )
        tag_by_race: dict[int, str] = {}
        if ranked:
            tag_by_race[ranked[0].race_number] = "NAP"
        if len(ranked) > 1:
            tag_by_race[ranked[1].race_number] = "NB"

        for pick in meeting.picks:
            tag = tag_by_race.get(pick.race_number, "")
            suffix = f"  {tag}" if tag else ""
            lines.append(f"R{pick.race_number} {pick.horse.name}{suffix}")
        lines.append("")
    return "\n".join(lines).strip()


def scrape_all() -> list[Meeting]:
    return [scrape_meeting(name, url) for name, url in get_upcoming_meetings()]


if __name__ == "__main__":
    meetings = scrape_all()
    if not meetings:
        print("No upcoming meetings found.")
    else:
        print(format_output(meetings))
