"""Prefer the original order while separating articles from the same source."""

from collections import deque


def diversify_sources(rows, source_for, *, limit=None, prefer_first=False):
    """Keep each source's internal order and avoid adjacent repeats when possible."""
    queues = {}
    for position, row in enumerate(rows):
        queues.setdefault(source_for(row), deque()).append((position, row))

    ordered = []
    previous = None
    remaining = len(rows)
    while queues and (not limit or len(ordered) < limit):
        eligible = [source for source in queues if source != previous]
        if not eligible:
            eligible = list(queues)
        # A source with more than half the remaining rows must take this slot
        # to avoid creating an unnecessary run at the end of the list.
        dominant = next((source for source in eligible if len(queues[source]) * 2 > remaining), None)
        source = dominant if dominant is not None and limit is None and (ordered or not prefer_first) else min(
            eligible, key=lambda key: queues[key][0][0]
        )
        _, row = queues[source].popleft()
        if not queues[source]:
            del queues[source]
        ordered.append(row)
        previous = source
        remaining -= 1
    return ordered
