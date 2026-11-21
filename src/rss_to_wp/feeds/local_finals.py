"""Use confirmed scoreboards from the newsroom's varsity basketball calendar."""

from datetime import datetime, timedelta
from html import escape
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
            label = cells[1].select_one("a .name")
            if label is None:
                continue
            opponent = label.get_text(" ", strip=True)
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
    if other[3] and not winner[3]:
        title = f"{other[0]} {league} fall to {winner[0]} {winner[1]}–{other[1]}"
    else:
        title = f"{winner[0]} {league} beat {other[0]} {winner[1]}–{other[1]}"
    day = event["date"]
    lead = f"{winner[0]} beat {other[0]} {winner[1]}–{other[1]} in {league} varsity basketball {day.strftime('%A, %B %d').replace(' 0',' ')}, according to the teams’ final box score on MaxPreps."
    paragraphs = [lead]
    quarter_lines = {}
    for row, (name, score, path, local) in zip(rows, teams):
        numbers = [node.get_text(strip=True) for node in row.select("td:not(.final-score)")]
        if len(numbers) == len(headings)-2 and all(value.isdigit() for value in numbers):
            if sum(map(int,numbers)) != score:
                raise ValueError("Period scores disagree with the final")
            quarter_lines[name] = list(map(int,numbers))
        current, past = recent_results(path, url, day)
        if current != ("W" if name == winner[0] else "L", winner[1], other[1]):
            if local or current is not None:
                return None
            continue
        if past:
            previous = []
            for played, opponent, outcome, high, low in reversed(past):
                verb = "beat" if outcome == "W" else "lost to"
                previous.append(f"{verb} {opponent} {high}–{low} on {played.strftime('%B %d').replace(' 0',' ')}")
            paragraphs.append(f"{name} " + " and ".join(previous) + f" before {day.strftime('%A')}’s game.")
    if len(quarter_lines) == 2 and headings[1:5] == ["Q1","Q2","Q3","Q4"]:
        w, o = quarter_lines[winner[0]], quarter_lines[other[0]]
        first, second = sum(w[:2]), sum(o[:2])
        halftime = (f"The teams went to halftime tied at {first}." if first == second else
                    f"{winner[0] if first > second else other[0]} led {max(first,second)}–{min(first,second)} at halftime.")
        quarters = f"The first quarter ended with {winner[0]} at {w[0]} and {other[0]} at {o[0]}. {halftime} In the fourth quarter {winner[0]} scored {w[3]} points and {other[0]} scored {o[3]}."
        paragraphs.insert(1, quarters)
    if len(paragraphs) < 3:
        return None
    body = "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
    return FeedParserDict(id=url, title=title, link=url, summary=body, score_brief=body,
                          published=event["start_dt"].isoformat())


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
