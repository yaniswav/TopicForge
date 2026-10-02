"""Topic-name filters that understand ROS 2 name mangling: binding-free.

ROS 2 publishes the topic `/scan` as the DDS topic `rt/scan`, so an agent that
asks for `rt/scan` on a bare-DDS bus (or `scan` on a ROS 2 bus) must still
find it. `resolve_topic_filter` tries the exact name first, then the alternate
forms, and says which one matched.
"""

from __future__ import annotations

from collections.abc import Iterable

_MAX_NOTE_TOPICS = 5


def levenshtein(a: str, b: str, max_distance: int | None = None) -> int:
    """Edit distance between two strings (insert, delete, substitute).

    With `max_distance`, any distance above it returns `max_distance + 1`, and
    the work is bounded: the common prefix and suffix are stripped first, then
    only a diagonal band of the table is computed.
    """
    if a == b:
        return 0
    if max_distance is None:
        return _full_distance(a, b)
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    start = 0
    limit = min(len(a), len(b))
    while start < limit and a[start] == b[start]:
        start += 1
    a, b = a[start:], b[start:]
    while a and b and a[-1] == b[-1]:
        a, b = a[:-1], b[:-1]
    if not a or not b:
        return min(len(a) + len(b), max_distance + 1)
    return _banded_distance(a, b, max_distance)


def _full_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _banded_distance(a: str, b: str, k: int) -> int:
    """Edit distance when it is at most `k`, else `k + 1`; only cells within `k` of the diagonal."""
    over = k + 1
    prev = {j: j for j in range(min(len(b), k) + 1)}
    for i in range(1, len(a) + 1):
        cur: dict[int, int] = {}
        for j in range(max(0, i - k), min(len(b), i + k) + 1):
            if j == 0:
                cur[j] = i
                continue
            cur[j] = min(
                prev.get(j, over) + 1,
                cur.get(j - 1, over) + 1,
                prev.get(j - 1, over) + (a[i - 1] != b[j - 1]),
            )
        if min(cur.values()) > k:
            return over
        prev = cur
    return min(prev.get(len(b), over), over)


def alternate_forms(topic: str) -> list[str]:
    """Other spellings of `topic`: `rt/x` <-> `x` <-> `/x`, in the order they are tried."""
    if topic.startswith("rt/"):
        bare = topic[3:]
        forms = [bare, "/" + bare]
    elif topic.startswith("/"):
        bare = topic.lstrip("/")
        forms = ["rt" + topic, bare]
    else:
        forms = ["rt/" + topic, "/" + topic]
    return [f for f in forms if f and f != topic]


def resolve_topic_filter(topic: str, known: Iterable[str]) -> tuple[str | None, str | None]:
    """The known topic a filter selects and a note when it was not an exact match.

    Exact name first, then the alternate forms. Returns `(None, None)` when
    nothing matches.
    """
    names = set(known)
    if topic in names:
        return topic, None
    for form in alternate_forms(topic):
        if form in names:
            return form, f"filter {topic!r} matched the DDS topic {form!r} (alternate name form)"
    return None, None


def no_match_note(topic: str, known: Iterable[str]) -> str:
    """Note for a filter that matched nothing: the closest known topics first."""
    names = sorted(set(known), key=lambda t: (levenshtein(topic, t), t))
    if not names:
        return f"no endpoint on {topic!r}; no endpoint is known on this domain yet"
    shown = ", ".join(names[:_MAX_NOTE_TOPICS])
    return f"no endpoint on {topic!r}; known topics: {shown}"
