from datetime import date
from unittest.mock import Mock

import pytest
import requests

from rss_to_wp import gameday
from rss_to_wp.feeds import calendar_parser
from rss_to_wp.wordpress import WordPressClient


def test_unavailable_calendar_is_not_an_empty_schedule(monkeypatch):
    monkeypatch.setattr(calendar_parser, "fetch_ics_calendar", lambda url: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        calendar_parser.get_todays_games()
    with pytest.raises(ValueError):
        calendar_parser.parse_ics_events("Service temporarily unavailable")


def test_midnight_utc_and_date_only_game_times():
    calendar = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Boys Basketball: Ripley vs Walnut
DTSTART:20261118T010000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Girls Basketball: Ripley vs Walnut
DTSTART;VALUE=DATE:20261117
END:VEVENT
END:VCALENDAR"""
    games = calendar_parser.parse_ics_events(calendar)
    assert [g["date"] for g in games] == [date(2026, 11, 17)] * 2
    assert [g["time"] for g in games] == ["7:00 PM", "Time not listed"]


@pytest.mark.parametrize("status", ["publish", "draft"])
def test_recovery_reconciles_legacy_publication_before_writing(monkeypatch, status):
    client, store, rewrite = Mock(), Mock(), Mock()
    store.is_processed.return_value = False
    client.find_post_by_slug.return_value = {"id": 8540, "status": status}
    monkeypatch.setattr(gameday, "get_todays_games", lambda **kw: [object()])
    monkeypatch.setattr(gameday, "DedupeStore", lambda: store)
    monkeypatch.setattr(gameday, "WordPressClient", lambda **kw: client)
    monkeypatch.setattr(gameday, "OpenAIRewriter", rewrite)
    if status == "publish":
        assert gameday.generate_gameday_article(Mock(), target_date=date(2026, 11, 17))["skipped"]
    else:
        with pytest.raises(RuntimeError, match="draft"):
            gameday.generate_gameday_article(Mock(), target_date=date(2026, 11, 17))
    client.find_post_by_slug.assert_called_once_with("tippah-county-sports-happening-today-november-17-2026")
    rewrite.assert_not_called()
    client.create_post.assert_not_called()


def test_lookup_failure_cannot_create_a_second_edition():
    client = WordPressClient("https://example.org", "editor", "unused")
    client.session = Mock()
    client.session.get.side_effect = requests.Timeout
    with pytest.raises(requests.Timeout):
        client.find_post_by_slug("today")
    client.session.post.assert_not_called()
