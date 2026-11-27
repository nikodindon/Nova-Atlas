"""Select radio stories by their source publication date and editorial evidence."""
import hashlib
import json
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Europe/Paris")
BLOCKING_RULES = frozenset({"municipal_election_vs_standalone_match_v1"})


def publication_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return date if date and date.tzinfo is not None else None


def blocked_on_air(article, records):
    record = records.get(article.get("hash"), {})
    if not isinstance(record, dict):
        return False
    review = record.get("editorial_review")
    if not isinstance(review, dict):
        return False
    digest = hashlib.sha256(article["summary"].encode()).hexdigest()
    return review.get("summary_sha256") == digest and any(
        isinstance(item, dict) and item.get("rule") in BLOCKING_RULES
        for item in (review.get("evidence") or [])
    )


def select_for_radio(articles, *, now, minimum, limit, fresh_minutes, recap_minutes, records):
    dated = []
    for article in articles:
        if blocked_on_air(article, records):
            continue
        published = publication_time(article.get("pub_date"))
        if published and now - timedelta(minutes=recap_minutes) <= published <= now:
            dated.append((published, article))
    dated.sort(key=lambda row: row[0], reverse=True)
    fresh = [article for published, article in dated if published >= now - timedelta(minutes=fresh_minutes)]
    if len(fresh) >= minimum:
        return fresh[:limit], "fresh"
    recap = [article for _, article in dated[:limit]]
    return (recap, "recap") if len(recap) >= minimum else ([], "not_enough_recent_articles")


def load_review_records(data_dir):
    path = Path(data_dir) / "article_processing.json"
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text())
        records = data.get("records", {}) if isinstance(data, dict) else {}
        return records if isinstance(records, dict) else {}
    except (OSError, ValueError):
        return {}
