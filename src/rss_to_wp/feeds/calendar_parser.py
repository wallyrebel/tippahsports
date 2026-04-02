"""ICS calendar parser for daily game day articles.

Fetches and parses the autoschedule ICS calendar, filters events to today's
games in America/Chicago timezone, and deduplicates paired home/away entries.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

import pendulum
import requests
from icalendar import Calendar

from rss_to_wp.utils import get_logger

logger = get_logger("feeds.calendar_parser")

# The hosted ICS calendar URL (from GitHub Pages via autoschedule)
ICS_CALENDAR_URL = "https://wallyrebel.github.io/autoschedule/schedules.ics"

# Sport emoji mapping for display
SPORT_EMOJIS = {
    "baseball": "⚾",
    "softball": "🥎",
    "basketball": "🏀",
    "football": "🏈",
    "soccer": "⚽",
    "volleyball": "🏐",
    "track": "🏃",
    "cross country": "🏃",
    "golf": "⛳",
    "tennis": "🎾",
    "cheer": "📣",
    "bowling": "🎳",
}


def fetch_ics_calendar(url: str = ICS_CALENDAR_URL) -> Optional[str]:
    """Fetch raw ICS content from URL.

    Args:
        url: URL to the ICS calendar file.

    Returns:
        Raw ICS text content, or None on failure.
    """
    try:
        response = requests.get(url, timeout=(10, 30))
        response.raise_for_status()
        logger.info("ics_fetched", url=url, size=len(response.text))
        return response.text
    except Exception as e:
        logger.error("ics_fetch_error", url=url, error=str(e))
        return None


def parse_ics_events(ics_text: str) -> list[dict]:
    """Parse ICS text into a list of event dictionaries.

    Args:
        ics_text: Raw ICS calendar content.

    Returns:
        List of event dicts with parsed fields.
    """
    events = []

    try:
        cal = Calendar.from_ical(ics_text)
    except Exception as e:
        logger.error("ics_parse_error", error=str(e))
        return events

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        try:
            event = _parse_vevent(component)
            if event:
                events.append(event)
        except Exception as e:
            summary = str(component.get("SUMMARY", "unknown"))
            logger.warning("vevent_parse_error", summary=summary[:50], error=str(e))
            continue

    logger.info("ics_events_parsed", total=len(events))
    return events


def _parse_vevent(component) -> Optional[dict]:
    """Parse a single VEVENT component into a structured dict.

    Args:
        component: An icalendar VEVENT component.

    Returns:
        Structured game dict or None.
    """
    summary = str(component.get("SUMMARY", ""))
    description = str(component.get("DESCRIPTION", ""))
    location = str(component.get("LOCATION", ""))
    url = str(component.get("URL", ""))
    categories_prop = component.get("CATEGORIES")

    # Parse start time
    dtstart = component.get("DTSTART")
    if not dtstart:
        return None

    dt = dtstart.dt
    # Convert to pendulum for timezone handling
    if hasattr(dt, "hour"):
        # It's a datetime
        start_dt = pendulum.instance(dt, tz="America/Chicago")
    else:
        # It's a date-only
        start_dt = pendulum.parse(str(dt), tz="America/Chicago")

    # Parse categories
    categories = []
    if categories_prop:
        if hasattr(categories_prop, "to_ical"):
            cats_str = categories_prop.to_ical().decode("utf-8", errors="replace")
            categories = [c.strip() for c in cats_str.split(",")]
        elif isinstance(categories_prop, list):
            for cat_item in categories_prop:
                if hasattr(cat_item, "to_ical"):
                    cats_str = cat_item.to_ical().decode("utf-8", errors="replace")
                    categories.extend([c.strip() for c in cats_str.split(",")])

    # Parse sport from categories or summary
    sport = _detect_sport(categories, summary)

    # Parse teams from summary
    # Format: "🥎 Girls Softball: Team A vs Team B" or "Team A @ Team B"
    home_team, away_team, is_home = _parse_teams(summary)

    # Get emoji
    emoji = _get_sport_emoji(sport, summary)

    # Format time
    time_str = start_dt.format("h:mm A")

    return {
        "sport": sport,
        "emoji": emoji,
        "summary": summary,
        "home_team": home_team,
        "away_team": away_team,
        "is_home_game": is_home,
        "time": time_str,
        "date": start_dt.date(),
        "start_dt": start_dt,
        "location": location,
        "description": description,
        "url": url if url and url != "https://www.maxpreps.com" else "",
        "categories": categories,
    }


def _detect_sport(categories: list[str], summary: str) -> str:
    """Detect the sport type from categories or summary.

    Args:
        categories: Event categories list.
        summary: Event summary/title.

    Returns:
        Sport name string.
    """
    sport_keywords = [
        "Baseball", "Softball", "Basketball", "Football",
        "Soccer", "Volleyball", "Track", "Cross Country",
        "Golf", "Tennis", "Cheer", "Bowling",
    ]

    # Check categories first
    for cat in categories:
        for keyword in sport_keywords:
            if keyword.lower() in cat.lower():
                return keyword

    # Fall back to summary
    for keyword in sport_keywords:
        if keyword.lower() in summary.lower():
            return keyword

    return "Sports"


def _get_sport_emoji(sport: str, summary: str) -> str:
    """Get emoji for the sport.

    Args:
        sport: Sport name.
        summary: Event summary (may contain emoji already).

    Returns:
        Sport emoji character.
    """
    # Check if summary already starts with an emoji
    for emoji in SPORT_EMOJIS.values():
        if emoji in summary:
            return emoji

    return SPORT_EMOJIS.get(sport.lower(), "🏅")


def _parse_teams(summary: str) -> tuple[str, str, bool]:
    """Parse home and away teams from the event summary.

    Summary format examples:
        "⚾ Boys Baseball: Ripley Tigers vs Kossuth Aggies" (home game, team1 is home)
        "⚾ Boys Baseball: Walnut Wildcats @ Hamilton Lions" (away game, team1 is away)

    Args:
        summary: Event summary string.

    Returns:
        Tuple of (home_team, away_team, is_home_game).
    """
    # Remove emoji prefix and sport label
    clean = re.sub(r"^[^\w]*(?:Boys|Girls)\s+\w+:\s*", "", summary).strip()
    if not clean:
        # Try without gender prefix
        clean = re.sub(r"^[^\w]*\w+:\s*", "", summary).strip()
    if not clean:
        clean = summary

    # Check for "vs" (home) or "@" (away)
    if " vs " in clean:
        parts = clean.split(" vs ", 1)
        return parts[0].strip(), parts[1].strip(), True
    elif " @ " in clean:
        parts = clean.split(" @ ", 1)
        return parts[1].strip(), parts[0].strip(), False

    return clean, "", True


def get_todays_games(
    target_date: Optional[date] = None,
    ics_url: str = ICS_CALENDAR_URL,
) -> list[dict]:
    """Fetch and return today's games from the ICS calendar.

    This is the main entry point for the calendar parser.

    Args:
        target_date: Specific date to filter for (defaults to today in CST).
        ics_url: URL to the ICS calendar.

    Returns:
        List of deduplicated game dicts for today, sorted by time.
    """
    if target_date is None:
        target_date = pendulum.now("America/Chicago").date()

    logger.info("fetching_todays_games", date=str(target_date))

    # Fetch ICS
    ics_text = fetch_ics_calendar(ics_url)
    if not ics_text:
        logger.warning("no_ics_data")
        return []

    # Parse all events
    all_events = parse_ics_events(ics_text)

    # Filter to target date
    todays_events = [
        e for e in all_events
        if e["date"] == target_date
    ]

    logger.info(
        "todays_events_found",
        date=str(target_date),
        count=len(todays_events),
    )

    if not todays_events:
        return []

    # Deduplicate (ICS has both home and away entries for same matchup)
    deduped = _deduplicate_games(todays_events)

    # Sort by time
    deduped.sort(key=lambda e: e["start_dt"])

    logger.info(
        "todays_games_final",
        date=str(target_date),
        count=len(deduped),
    )

    return deduped


def _deduplicate_games(events: list[dict]) -> list[dict]:
    """Remove duplicate entries for the same matchup.

    The ICS calendar has separate entries for home and away perspectives
    of the same game. We keep the home team's entry.

    Args:
        events: List of event dicts.

    Returns:
        Deduplicated list.
    """
    seen = set()
    deduped = []

    for event in events:
        # Create a canonical key from sorted team names + sport + time
        teams = sorted([
            event["home_team"].lower().strip(),
            event["away_team"].lower().strip(),
        ])
        key = (
            tuple(teams),
            event["sport"].lower(),
            event["time"],
        )

        if key not in seen:
            seen.add(key)
            # Prefer the home game entry
            deduped.append(event)
        else:
            # If we already have an away entry, replace with home entry
            if event["is_home_game"]:
                deduped = [
                    e if (
                        sorted([e["home_team"].lower(), e["away_team"].lower()]),
                        e["sport"].lower(),
                        e["time"],
                    ) != (list(teams), event["sport"].lower(), event["time"])
                    else event
                    for e in deduped
                ]

    return deduped
