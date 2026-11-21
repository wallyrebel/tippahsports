from datetime import date
from unittest.mock import Mock

from rss_to_wp.feeds import local_finals as finals


URL = finals.BASE + "/ms/basketball/girls/game/ingomar-vs-walnut/11-20-2026/?c=" + "a"*36
DAY = date(2026, 11, 20)
EVENT = dict(url=URL, date=DAY, start_dt=DAY, home_team="Walnut Wildcats", away_team="Ingomar Falcons")


def test_unreported_games_and_season_averages_never_supply_a_final(monkeypatch):
    html = f'<link rel="canonical" href="{URL}"><table data-testid="box-score-table"><thead><tr><th>Final</th><th>Q1</th></tr></thead></table>'
    monkeypatch.setattr(finals, "get", Mock(return_value=html))
    assert finals.verified_entry(EVENT) is None
    monkeypatch.setattr(finals, "get", Mock(return_value='<div class="final">Final</div>'+html))
    assert finals.verified_entry(EVENT) is None


def test_final_needs_the_same_result_on_the_local_team_schedule(monkeypatch):
    def row(name, slug, points, quarters):
        return (f'<tr><th><a class="team-cell" href="/ms/town/{slug}/basketball/girls/">{name}</a></th>'
                + "".join(f"<td>{q}</td>" for q in quarters) + f'<td class="final-score">{points}</td></tr>')
    html = (f'<link rel="canonical" href="{URL}"><div class="final">Final</div><table data-testid="box-score-table">'
            '<thead><tr><th></th><th>Q1</th><th>Q2</th><th>Q3</th><th>Q4</th><th>Final</th></tr></thead><tbody>'
            + row("Ingomar", "ingomar-falcons", 42, [10,10,12,10])
            + row("Walnut", "walnut-wildcats", 60, [15,15,15,15]) + "</tbody></table>")
    monkeypatch.setattr(finals, "get", Mock(return_value=html))
    monkeypatch.setattr(finals, "recent_results", Mock(return_value=(("L",60,42),[])))
    assert finals.verified_entry(EVENT) is None
    def schedule(path, *args):
        won = "walnut" in path
        return (("W" if won else "L",60,42), [(date(2026,11,17),"Myrtle","W",42,33)])
    monkeypatch.setattr(finals, "recent_results", schedule)
    assert "Walnut girls beat Ingomar 60–42" in finals.verified_entry(EVENT).title
