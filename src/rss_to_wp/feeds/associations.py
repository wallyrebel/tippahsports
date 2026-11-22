"""Select Tippah County coverage from statewide and conference news feeds."""

import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from feedparser import FeedParserDict

from rss_to_wp.content_quality import plain_text
from rss_to_wp.feeds.golf import local_golf

STATE = "https://www.misshsaa.com/feed/"
CONFERENCE = "https://ssacsports.com/rss.aspx"
NAMES = {
    STATE: "Mississippi High School Activities Association",
    CONFERENCE: "Southern States Athletic Conference",
}
HIGH_SCHOOLS = re.compile(
    r"\b(?:Ripley|Falkner|Walnut(?! Grove)|Pine Grove|Blue Mountain)\b", re.I
)


def local_releases(feed_url, feed):
    if feed_url not in NAMES:
        return feed
    host = urlsplit(feed_url).netloc
    if feed.feed.get("title") != NAMES[feed_url] or urlsplit(feed.feed.get("link", "")).netloc != host:
        raise ValueError("Sports association identity changed")
    entries = {}
    for entry in feed.entries:
        title = plain_text(entry.get("title", ""))
        link = urlsplit(entry.get("link", ""))
        if link.scheme not in {"http", "https"} or link.netloc != host:
            continue
        if feed_url == STATE:
            if not HIGH_SCHOOLS.search(title):
                continue
        else:
            # The university's own feeds already cover routine conference awards.
            # Select national honors and bids that explicitly involve Blue Mountain.
            lead = plain_text(entry.get("summary", ""))
            if not (re.search(r"\b(?:NAIA|national)\b", title, re.I)
                    and re.search(r"\bBlue Mountain (?:Christian|College)\b", title + " " + lead, re.I)):
                continue
        dated = re.fullmatch(r"(?:/news)?/(\d{4}/\d{1,2}/\d{1,2})/[^/]+/?", link.path)
        if not dated or not title:
            continue
        # The conference sometimes publishes one release at two URLs minutes apart.
        identity = f"{host}/{dated.group(1)}/{' '.join(title.casefold().split())}"
        item = entry.copy()
        item["link"] = link._replace(scheme="https", fragment="").geturl()
        item["id"] = identity
        entries.setdefault(identity, item)
    if feed_url == STATE:
        # Keep the existing identity for a local headline that was already processed.
        other_titles = [item for item in feed.entries if not HIGH_SCHOOLS.search(plain_text(item.get("title", "")))]
        cards = FeedParserDict(feed=feed.feed, entries=other_titles)
        schools = {"Pine Grove Panthers": "Pine Grove", "Ripley Tigers": "Ripley", "Falkner Eagles": "Falkner",
                   "Walnut Wildcats": "Walnut", "Blue Mountain Cougars": "Blue Mountain"}
        for result in local_golf(cards, schools).entries:
            entries.setdefault(result.link, result)
    feed.entries = list(entries.values())
    return feed


def conference_report(url, title):
    """Read the matched release and remove the footer listing previous winners."""
    if urlsplit(url).netloc != "ssacsports.com" or not url.startswith("https://ssacsports.com/news/"):
        raise ValueError("Unexpected conference release URL")
    response = requests.get(url, timeout=(5, 15), allow_redirects=False,
                            headers={"User-Agent": "TippahCountySports/1.0 (+https://tippahsports.com/)"})
    if response.status_code != 200:
        raise ValueError("Conference release unavailable")
    page = BeautifulSoup(response.content, "html.parser")
    identity = page.select_one('meta[property="og:url"]')
    headline = page.select_one('meta[property="og:title"]')
    body = page.select_one(".sidearm-story-template-text")
    if not body or not identity or identity.get("content") != url or not headline or plain_text(headline.get("content", "")) != plain_text(title):
        raise ValueError("Conference release identity or article body changed")
    for extra in body.select("aside,script,style,footer"):
        extra.decompose()
    for heading in body.select("strong,b,h2,h3,h4"):
        if re.fullmatch(r"\d{4}[-–]\d{2,4}\s+(?:NAIA\s+)?Winners", heading.get_text(" ", strip=True), re.I):
            for sibling in list(heading.next_siblings):
                sibling.extract()
            heading.decompose()
            break
    # Preserve the lead, date and local athlete's performance. In a multi-athlete
    # bulletin a different player's stat line is irrelevant to the local profile.
    for heading in body.select("strong,b"):
        label = heading.get_text(" ", strip=True)
        stats = heading.find_next_sibling()
        if "(" in label and ")" in label and "blue mountain" not in label.casefold() and stats and stats.name == "ul":
            stats.decompose()
            heading.decompose()
    text = body.get_text(" ", strip=True)
    if not re.search(r"\bBlue Mountain (?:Christian|College)\b", text, re.I):
        raise ValueError("Conference release does not establish a local connection")
    return text
