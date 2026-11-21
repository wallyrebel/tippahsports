"""Use confirmed scoreboards from the newsroom's varsity basketball calendar."""

from datetime import datetime, timedelta
import re
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from feedparser import FeedParserDict
import requests

from rss_to_wp.feeds.calendar_parser import ICS_CALENDAR_URL, parse_ics_events
from rss_to_wp.utils import get_logger

BASE = "https://www.maxpreps.com"
ZONE = ZoneInfo("America/Chicago")
GAME = re.compile(re.escape(BASE) + r"/ms/basketball/(girls/)?game/[a-z0-9-]+/(\d{1,2}-\d{1,2}-\d{4})/\?c=[a-f0-9-]{36}")
LOCAL = {"pine-grove-panthers", "walnut-wildcats", "falkner-eagles", "ripley-tigers", "blue-mountain-cougars"}


def get(url):
    response = requests.get(url, timeout=(5, 15), allow_redirects=False, headers={
        "User-Agent": "TippahCountySports/1.0 (+https://tippahsports.com/)",
        "Accept-Encoding": "gzip, deflate",
    })
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Calendar or team record unavailable")
    return response.content


def recent_results(team_link, game, day):
    document = BeautifulSoup(get(urljoin(BASE, team_link) + "schedule/"), "html.parser")
    results = []
    current = None
    for row in document.select("table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3 or not cells[0].a or not cells[1].a:
            continue
        target = urljoin(BASE, cells[0].a.get("href", ""))
        match = GAME.fullmatch(target)
        result = re.fullmatch(r"([WL])\s*(\d+)[-–](\d+)", cells[2].get_text(" ", strip=True))
        if not match or not result:
            continue
        played = datetime.strptime(match[2], "%m-%d-%Y").date()
        if target == game:
            current = (result[1], max(int(result[2]), int(result[3])), min(int(result[2]), int(result[3])))
        elif day - timedelta(days=15) <= played < day:
            opponent = re.sub(r"^(?:vs\.?|@)\s*", "", cells[1].a.get_text(" ", strip=True))
            results.append((played, opponent, result[1], max(int(result[2]),int(result[3])), min(int(result[2]),int(result[3]))))
    return current, sorted(set(results), reverse=True)[:2]


def verified_entry(event):
    url = event["url"]
    match = GAME.fullmatch(url)
    if not match or datetime.strptime(match[2], "%m-%d-%Y").date() != event["date"]:
        return None
    page = BeautifulSoup(get(url), "html.parser")
    canonical = page.select_one('link[rel="canonical"]')
    if not canonical or canonical.get("href") != url:
        raise ValueError("Game record identity changed")
    table = page.select_one('table[data-testid="box-score-table"]')
    if not table or not page.select_one(".final") or page.select_one(".final").get_text(strip=True) != "Final":
        return None
    headings = [h.get_text(strip=True) for h in table.select("thead th")]
    rows = table.select("tbody tr")
    if len(rows) != 2 or not headings or headings[-1] != "Final":
        return None
    teams = []
    for row in rows:
        anchor = row.select_one("th a.team-cell")
        total = row.select_one("td.final-score")
        if not anchor or not total or not total.get_text(strip=True).isdigit():
            return None
        path = anchor.get("href", "")
        team = re.fullmatch(r"/ms/[a-z-]+/([a-z0-9-]+)/basketball/(girls/)?", path)
        if not team or bool(team[2]) != bool(match[1]):
            return None
        name = anchor.get_text(" ", strip=True)
        if not any(re.search(r"\b"+re.escape(name)+r"\b", event.get(k,""), re.I) for k in ("home_team","away_team")):
            return None
        teams.append((name, int(total.get_text(strip=True)), path, team[1] in LOCAL))
    if not any(t[3] for t in teams) or teams[0][1] == teams[1][1]:
        return None
    winner, other = sorted(teams, key=lambda t: t[1], reverse=True)
    league = "girls" if match[1] else "boys"
    title = f"{winner[0]} defeats {other[0]} {winner[1]}–{other[1]} in {league} basketball"
    day = event["date"]
    evidence = [f"In varsity {league} basketball on {day.strftime('%B %d, %Y')}, {winner[0]} won {winner[1]} to {other[1]} against {other[0]}, according to the final MaxPreps team box score."]
    for row, (name, score, path, local) in zip(rows, teams):
        numbers = [node.get_text(strip=True) for node in row.select("td:not(.final-score)")]
        if len(numbers) == len(headings)-2 and all(value.isdigit() for value in numbers):
            if sum(map(int,numbers)) != score:
                raise ValueError("Period scores disagree with the final")
            evidence.append(f"{name} scoring by period: " + ", ".join(f"{label} {value}" for label,value in zip(headings[1:-1],numbers)) + ".")
        if local:
            current, past = recent_results(path, url, day)
            if current != ("W" if name == winner[0] else "L", winner[1], other[1]):
                return None
            for played, opponent, outcome, high, low in past:
                verb = "beat" if outcome == "W" else "lost to"
                evidence.append(f"On {played.strftime('%B %d')}, {name} {verb} {opponent} {high}–{low}, according to the team's varsity {league} schedule.")
    if len(evidence) < 3:
        return None
    return FeedParserDict(id=url, title=title, link=url, summary=" ".join(evidence), published=event["start_dt"].isoformat())


def local_finals():
    now = datetime.now(ZONE)
    events = parse_ics_events(get(ICS_CALENDAR_URL).decode("utf-8-sig"))
    if not events:
        raise ValueError("Calendar has no identifiable events")
    entries = []
    seen = set()
    errors = 0
    for event in sorted(events, key=lambda x:x["start_dt"], reverse=True):
        if event["sport"] != "Basketball" or event["url"] in seen or not now - timedelta(hours=48) <= event["start_dt"] <= now:
            continue
        seen.add(event["url"])
        try:
            entry = verified_entry(event)
        except (ValueError, requests.RequestException) as exc:
            get_logger("feeds.local_finals").warning("scoreboard_unavailable", url=event["url"], error_type=type(exc).__name__)
            errors += 1
            continue
        if entry:
            entries.append(entry)
    if errors and not entries:
        raise ValueError("Unable to verify one or more pending scoreboards")
    return FeedParserDict(entries=entries, feed={"title":"Tippah County varsity basketball results"})
