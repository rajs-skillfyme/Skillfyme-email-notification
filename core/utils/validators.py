"""
core/utils/validators.py
-------------------------
Shared utility functions — verbatim copy from app/utils/validators.py.

  - is_valid_email()          : basic email format check
  - derive_name_from_email()  : convert email prefix → display name
  - get_upcoming_class_dates(): generate future class dates for a batch
  - parse_iso_datetime()      : parse ISO datetime strings
"""

from __future__ import annotations
import re
import logging
from datetime import date, datetime, timedelta
from typing import List

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Weekday name → Python weekday() integer (Monday = 0)
_DAY_MAP = {
    'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3,
    'Fri': 4, 'Sat': 5, 'Sun': 6,
}


def is_valid_email(email: str) -> bool:
    """Return True if *email* looks like a valid address."""
    return bool(_EMAIL_RE.match(email.strip()))


def derive_name_from_email(email: str) -> str:
    """
    Derive a human-readable display name from an email address.

    Examples
    --------
    jayanthdolai@gmail.com   → 'Jayanthdolai'
    a.nagaraj1981@yahoo.com  → 'A Nagaraj'
    john_doe_99@outlook.com  → 'John Doe'
    """
    prefix = email.split('@')[0]
    # Replace dots, underscores, hyphens with spaces
    name = re.sub(r'[._\-]+', ' ', prefix)
    # Remove trailing/leading digits per segment
    parts = [re.sub(r'\d+', '', p) for p in name.split()]
    # Drop empty parts that result from all-digit segments
    parts = [p for p in parts if p]
    if not parts:
        # Fallback: use the whole prefix title-cased
        return prefix.title()
    return ' '.join(p.capitalize() for p in parts)


def get_upcoming_class_dates(
    class_days_str: str,
    batch_start_date: date,
    batch_end_date: date,
    from_date: date | None = None,
) -> List[date]:
    """
    Return a sorted list of all class dates between *from_date* (inclusive)
    and *batch_end_date* (inclusive) that fall on the configured class days.
    """
    day_integers: set[int] = set()
    for d in class_days_str.split(','):
        d = d.strip()
        if d in _DAY_MAP:
            day_integers.add(_DAY_MAP[d])
        else:
            logger.warning('Unknown day abbreviation %r — ignored.', d)

    start = max(from_date or date.today(), batch_start_date)
    result: List[date] = []
    current = start
    while current <= batch_end_date:
        if current.weekday() in day_integers:
            result.append(current)
        current += timedelta(days=1)
    return result


def parse_iso_datetime(value: str) -> datetime | None:
    """
    Parse an ISO-8601 datetime string (possibly with trailing 'Z') to a
    naive datetime object. Returns None if parsing fails.
    """
    if not value or not isinstance(value, str):
        return None
    value = value.strip().rstrip('Z')
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    logger.debug('Could not parse datetime string: %r', value)
    return None
