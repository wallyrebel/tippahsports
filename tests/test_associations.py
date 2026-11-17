from types import SimpleNamespace

import pytest

from rss_to_wp.feeds.associations import CONFERENCE, NAMES, STATE, conference_report, local_releases
from rss_to_wp.feeds.filter import generate_entry_key


def association(source, items):
    home = "http://ssacsports.com" if source == CONFERENCE else "https://www.misshsaa.com"
    return SimpleNamespace(feed={"title": NAMES[source], "link": home}, entries=items)


def test_national_notices_use_one_key_for_two_conference_permalinks():
    base = {"title": "Marco named NAIA Defensive Player of the Week",
            "summary": "The conference announced national honors for Terrence Marco of Blue Mountain Christian."}
    items = [dict(base, link="http://ssacsports.com/news/2026/11/17/" + slug) for slug in ("short.aspx", "long.aspx")]
    rows = local_releases(CONFERENCE, association(CONFERENCE, [
        *items, dict(items[0], title="SSAC Defensive Player of the Week"),
        dict(items[0], summary="A player from another university."), dict(items[0], link="https://example.com/report"),
    ])).entries
    assert len(rows) == 1 and rows[0]["link"].startswith("https://ssacsports.com/")
    alternate = local_releases(CONFERENCE, association(CONFERENCE, items[1:])).entries[0]
    assert generate_entry_key(rows[0], CONFERENCE) == generate_entry_key(alternate, CONFERENCE)


def test_state_feed_requires_a_named_local_school_and_confirmed_publisher():
    base = {"link": "https://www.misshsaa.com/2026/11/17/schedule/"}
    feed = association(STATE, [dict(base, title=x) for x in ("State brackets", "Walnut Grove update", "Falkner football")])
    assert [e["title"] for e in local_releases(STATE, feed).entries] == ["Falkner football"]
    feed.feed["title"] = "Unknown"
    with pytest.raises(ValueError, match="identity"):
        local_releases(STATE, feed)


def test_report_keeps_local_stats_and_omits_historical_awards(monkeypatch):
    url = "https://ssacsports.com/news/2026/11/17/award.aspx"
    html = f'''<meta property="og:url" content="{url}"><meta property="og:title" content="National honors">
    <div class="sidearm-story-template-text"><aside>Story links</aside>
    NAIA announced honors for the week of Nov. 9–15.
    <strong>Perkins (Xavier)</strong><ul><li>Scored 31 points per game.</li></ul>
    <strong>Marco (Blue Mountain Christian)</strong><ul><li>Blocked seven per game.</li></ul>
    <strong>2025-26 Winners</strong>Unrelated old result</div>'''
    monkeypatch.setattr("requests.get", lambda *a, **kw: SimpleNamespace(status_code=200, content=html))
    text = conference_report(url, "National honors")
    assert "Blocked seven" in text and "week of Nov." in text
    assert all(x not in text for x in ("31 points", "Unrelated old result", "Story links"))
    with pytest.raises(ValueError, match="identity"):
        conference_report(url, "Unrelated title")
