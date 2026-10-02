"""Topic-name filters that understand ROS 2 name mangling: binding-free.

ROS 2 publishes the topic `/scan` as the DDS topic `rt/scan`, so an agent that
asks for `rt/scan` on a bare-DDS bus (or `scan` on a ROS 2 bus) must still
find it. `resolve_topic_filter` tries the exact name first, then the alternate
forms, and says which one matched.
"""

from __future__ import annotations

from collections.abc import Iterable

_MAX_NOTE_TOPICS = 5


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two strings (insert, delete, substitute)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


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
