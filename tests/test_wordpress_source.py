from unittest.mock import Mock

import pytest

from rss_to_wp.wordpress.client import WordPressClient


def client_with(*pages):
    client = WordPressClient("https://news.test", "user", "test")
    client._rate_limit = Mock()
    client.session = Mock()
    client.session.get.side_effect = pages
    return client


def response(rows, pages=1):
    result = Mock(headers={"X-WP-TotalPages": str(pages)})
    result.json.return_value = rows
    return result


def test_season_url_does_not_match_a_boxscore_below_it():
    url = "https://school.test/stats/2026-27"
    article = {"content": {"rendered": f'<a href="{url}/school/boxscore/5884">Box score</a>'}}
    assert not client_with(response([article])).check_duplicate_by_source_url(url)


@pytest.mark.parametrize("content", [
    '<a href="https://school.test/?a=1&amp;b=2">Statistics</a>',
    'Source: https://school.test/?a=1&amp;b=2.',
])
def test_exact_source_on_a_later_page_is_preserved(content):
    client = client_with(response([], 2), response([{"content": {"rendered": content}}], 2))
    assert client.check_duplicate_by_source_url("https://school.test/?a=1&b=2")


def test_reuse_requires_the_full_attachment_url():
    url = "https://news.test/wp-content/uploads/2026/11/record.jpg"
    client = client_with(response([{"id": 17, "source_url": url}]))
    assert client.find_hosted_media("https://other.test/record.jpg") is None
    assert client.find_hosted_media(url) == 17
    other = url.replace("/11/", "/10/")
    with pytest.raises(ValueError):
        client_with(response([{"id": 17, "source_url": other}])).find_hosted_media(url)
