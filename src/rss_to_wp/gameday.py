"""Game Day article generator.

Fetches today's games from the ICS calendar, uses the OpenAI rewriter
to generate an AP-style preview article, and publishes to WordPress.
Every article includes a link to the full schedule page.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import pendulum

from rss_to_wp.config import AppSettings
from rss_to_wp.feeds.calendar_parser import get_todays_games
from rss_to_wp.rewriter import OpenAIRewriter
from rss_to_wp.storage import DedupeStore
from rss_to_wp.utils import get_logger
from rss_to_wp.wordpress import WordPressClient

logger = get_logger("gameday")

# The schedule page URL to include in every article
SCHEDULE_PAGE_URL = "https://tippahsports.com/schedule/"

# Category for game day articles
GAMEDAY_CATEGORY = "Tippah County Sports"

# Fallback image path (relative to project root)
FALLBACK_IMAGE = "assets/tippah_sports_logo.jpg"

# Custom system prompt for game day articles
GAMEDAY_SYSTEM_PROMPT = """You are a local sports news writer for Tippah County, Mississippi. 
You write enthusiastic but professional game-day preview articles for high school sports.

RULES:
1. Write in an engaging, community-focused tone
2. Use short, punchy paragraphs
3. Mention each game with teams, sport, time, and location
4. Group games by sport when possible (Baseball together, Softball together, etc.)
5. Highlight home games for local teams
6. Do NOT fabricate any information not provided
7. Include the schedule page URL naturally in the article body
8. Keep it concise but exciting - these are community sports previews

OUTPUT FORMAT:
You must respond with valid JSON in this exact format:
{
    "headline": "The headline exactly as provided - DO NOT CHANGE IT",
    "excerpt": "One to two sentence preview summary",
    "body": "Full article body in HTML format with <p> tags for paragraphs",
    "tags": ["tag1", "tag2", "tag3"]
}

TAG RULES:
- Include "Game Day" as a tag
- Include each team name mentioned
- Include each sport type (e.g., "Baseball", "Softball")
- Include "High School Sports"
- Tags should be capitalized properly

IMPORTANT:
- The body should be 3-8 paragraphs depending on number of games
- Use <p> tags to wrap each paragraph
- Do NOT change the headline - use it exactly as provided
- Do NOT include any markdown - use HTML only
- MUST include a link to the schedule page in the article body
"""


def build_games_summary(games: list[dict], target_date: date) -> str:
    """Build a plain text summary of today's games for the rewriter.

    Args:
        games: List of game dicts from calendar_parser.
        target_date: The date for the games.

    Returns:
        Formatted text summary of all games.
    """
    date_str = pendulum.instance(
        pendulum.datetime(target_date.year, target_date.month, target_date.day)
    ).format("dddd, MMMM D, YYYY")

    lines = [
        f"TIPPAH COUNTY SPORTS SCHEDULE FOR {date_str}",
        f"Total games today: {len(games)}",
        "",
    ]

    # Group by sport
    sports: dict[str, list[dict]] = {}
    for game in games:
        sport = game["sport"]
        if sport not in sports:
            sports[sport] = []
        sports[sport].append(game)

    for sport, sport_games in sports.items():
        emoji = sport_games[0]["emoji"] if sport_games else "🏅"
        lines.append(f"--- {emoji} {sport} ---")

        for game in sport_games:
            home = game["home_team"]
            away = game["away_team"]
            time = game["time"]
            location = game["location"]

            if game["is_home_game"]:
                matchup = f"{home} vs {away} (Home)"
            else:
                matchup = f"{away} @ {home}"

            lines.append(f"  • {matchup}")
            lines.append(f"    Time: {time}")
            if location:
                lines.append(f"    Location: {location}")
            if game.get("url"):
                lines.append(f"    MaxPreps: {game['url']}")
            lines.append("")

    lines.append(f"\nSchedule page link to include: {SCHEDULE_PAGE_URL}")

    return "\n".join(lines)


def generate_headline(target_date: date) -> str:
    """Generate the game day headline.

    Format: "Tippah County Sports Happening Today, April 2, 2026"

    Args:
        target_date: The date for the headline.

    Returns:
        Formatted headline string.
    """
    dt = pendulum.datetime(target_date.year, target_date.month, target_date.day)
    date_str = dt.format("MMMM D, YYYY")
    return f"Tippah County Sports Happening Today, {date_str}"


def generate_gameday_article(
    settings: AppSettings,
    dry_run: bool = False,
    target_date: Optional[date] = None,
    config_path: str = "",
) -> Optional[dict]:
    """Generate and publish a game day preview article.

    This is the main entry point for the game day feature.

    Args:
        settings: Application settings.
        dry_run: If True, don't publish to WordPress.
        target_date: Specific date (defaults to today CST).
        config_path: Path to config directory for resolving image paths.

    Returns:
        WordPress post data if published, None otherwise.
    """
    if target_date is None:
        target_date = pendulum.now("America/Chicago").date()

    logger.info("gameday_starting", date=str(target_date), dry_run=dry_run)

    # 1. Fetch today's games
    games = get_todays_games(target_date=target_date)

    if not games:
        logger.info("no_games_today", date=str(target_date))
        return None

    logger.info("games_found", count=len(games), date=str(target_date))

    # 2. Check deduplication (only one article per day)
    dedupe_store = DedupeStore()
    dedupe_key = f"gameday-preview-{target_date.isoformat()}"

    if dedupe_store.is_processed(dedupe_key):
        logger.info("gameday_already_published", date=str(target_date))
        return None

    # 3. Build game summary text for the rewriter
    summary_text = build_games_summary(games, target_date)
    headline = generate_headline(target_date)

    logger.info(
        "gameday_summary_built",
        headline=headline,
        games_count=len(games),
        summary_length=len(summary_text),
    )

    # 4. Rewrite with OpenAI using custom game day prompt
    rewriter = OpenAIRewriter(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )

    rewritten = _rewrite_gameday(rewriter, summary_text, headline)

    if not rewritten:
        logger.error("gameday_rewrite_failed")
        return None

    # 5. Ensure headline is exactly what we want (override AI)
    rewritten["headline"] = headline

    # 6. Append schedule link CTA to body
    body_with_cta = _append_schedule_cta(rewritten["body"])
    rewritten["body"] = body_with_cta

    # 7. Handle dry run
    if dry_run:
        logger.info(
            "gameday_dry_run",
            headline=headline,
            body_length=len(rewritten["body"]),
            excerpt=rewritten.get("excerpt", "")[:100],
            games=len(games),
        )
        print(f"\n{'='*60}")
        print(f"HEADLINE: {headline}")
        print(f"EXCERPT: {rewritten.get('excerpt', '')}")
        print(f"TAGS: {rewritten.get('tags', [])}")
        print(f"BODY LENGTH: {len(rewritten['body'])} chars")
        print(f"{'='*60}")
        print(rewritten["body"])
        print(f"{'='*60}\n")
        return {"id": 0, "link": "dry-run://not-published"}

    # 8. Publish to WordPress
    wp_client = WordPressClient(
        base_url=settings.wordpress_base_url,
        username=settings.wordpress_username,
        password=settings.wordpress_app_password,
        default_status=settings.wordpress_post_status,
    )

    # Upload featured image (Tippah Sports logo)
    featured_media_id = _upload_logo(wp_client, config_path)

    # Get/create category
    category_id = wp_client.get_or_create_category(GAMEDAY_CATEGORY)

    # Get/create tags
    tags = rewritten.get("tags", [])
    if "Game Day" not in tags:
        tags.insert(0, "Game Day")
    if "High School Sports" not in tags:
        tags.append("High School Sports")
    tag_ids = wp_client.get_or_create_tags(tags)

    # Create post (no source_url since this is original content)
    post = wp_client.create_post(
        title=rewritten["headline"],
        content=rewritten["body"],
        excerpt=rewritten.get("excerpt", ""),
        category_id=category_id,
        tag_ids=tag_ids,
        featured_media_id=featured_media_id,
        source_url=None,  # Original content, no source attribution
    )

    if post and not post.get("skipped"):
        # Mark as processed
        dedupe_store.mark_processed(
            entry_key=dedupe_key,
            feed_url="gameday-preview",
            entry_title=headline,
            entry_link=post.get("link", ""),
            wp_post_id=post.get("id"),
            wp_post_url=post.get("link"),
        )

        logger.info(
            "gameday_published",
            post_id=post.get("id"),
            url=post.get("link"),
            headline=headline,
        )

    return post


def _rewrite_gameday(
    rewriter: OpenAIRewriter,
    summary_text: str,
    headline: str,
) -> Optional[dict]:
    """Use OpenAI to rewrite the games summary into an article.

    Args:
        rewriter: OpenAI rewriter instance.
        summary_text: Plain text summary of games.
        headline: The exact headline to use.

    Returns:
        Dict with headline, excerpt, body, tags or None.
    """
    import json
    import time

    from openai import OpenAI

    rewriter._rate_limit()

    user_prompt = f"""Write a game day preview article for Tippah County sports.

HEADLINE TO USE (do not change this): {headline}

TODAY'S GAMES:
{summary_text}

IMPORTANT: 
- Use the headline EXACTLY as provided above
- Include a link to the full schedule: {SCHEDULE_PAGE_URL}
- Format the link as: <a href="{SCHEDULE_PAGE_URL}">view the full schedule at TippahSports.com</a>
- Respond with valid JSON containing headline, excerpt, body, and tags."""

    try:
        api_params = {
            "model": rewriter.model,
            "messages": [
                {"role": "system", "content": GAMEDAY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }

        # Token parameter based on model
        if any(x in rewriter.model.lower() for x in ["5", "4.1", "4o", "o1", "o3", "o4"]):
            api_params["max_completion_tokens"] = 2500
        else:
            api_params["max_tokens"] = 2500

        if "o1" not in rewriter.model.lower():
            api_params["response_format"] = {"type": "json_object"}

        response = rewriter.client.chat.completions.create(**api_params)
        response_text = response.choices[0].message.content

        result = rewriter._parse_response(response_text)

        if result:
            logger.info(
                "gameday_rewrite_complete",
                headline=result["headline"][:50],
                body_length=len(result["body"]),
            )
            return result

        return None

    except Exception as e:
        logger.error("gameday_rewrite_error", error=str(e))
        return None


def _append_schedule_cta(body: str) -> str:
    """Append a schedule link call-to-action to the article body.

    Args:
        body: HTML article body.

    Returns:
        Body with appended schedule CTA.
    """
    cta = (
        f'\n\n<p><strong>📅 View the full schedule and stay up to date with all '
        f'Tippah County sports at <a href="{SCHEDULE_PAGE_URL}" '
        f'target="_blank" rel="noopener">TippahSports.com/schedule</a>.'
        f'</strong></p>'
    )
    return body + cta


def _upload_logo(
    wp_client: WordPressClient,
    config_path: str,
) -> Optional[int]:
    """Upload the Tippah Sports logo as featured image.

    Args:
        wp_client: WordPress client instance.
        config_path: Path to resolve relative image paths.

    Returns:
        Media ID or None.
    """
    from pathlib import Path

    config_dir = Path(config_path).parent if config_path else Path(".")
    logo_path = config_dir / FALLBACK_IMAGE

    if not logo_path.exists():
        # Try from current directory
        logo_path = Path(FALLBACK_IMAGE)

    if not logo_path.exists():
        logger.warning("logo_not_found", path=str(logo_path))
        return None

    try:
        with open(logo_path, "rb") as f:
            image_bytes = f.read()

        media_id = wp_client.upload_media(
            image_bytes=image_bytes,
            filename="tippah_sports_logo.jpg",
            alt_text="Tippah County Sports",
        )

        logger.info("logo_uploaded", media_id=media_id)
        return media_id

    except Exception as e:
        logger.error("logo_upload_error", error=str(e))
        return None
