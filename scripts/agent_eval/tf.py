"""Ask TopicForge a question through the broker.

python tf.py <port> list                       # the tools and their descriptions
python tf.py <port> list --brief               # one line per tool: name(inputs): first sentence
python tf.py <port> <tool> '<json arguments>'  # call one tool

`--host HOST` before the port reaches a broker that is not on this machine (default 127.0.0.1).
`list --brief` is about 2.5 KB; the full `list` is the whole catalogue a client receives (about
35 KB of JSON). Read the brief one first, then call a tool, or look at the full text of one only
when its line is not enough: `python tf.py <port> list --tool <name>`.
"""

import json
import re
import socket
import sys

BRIEF_SENTENCE_MAX = 130
BRIEF_CUT_MIN = 25
# A full stop ends a sentence unless it sits in `e.g.` or `i.e.`.
_SENTENCE_END = re.compile(r"(?<![A-Za-z]\.[A-Za-z])\.(?=\s+[A-Z`*(\"']|\s*$)")
# Most ROS 2 tools open with a lane tag ("ROS 2 graph only; on a DDS-only setup use ...").
_ROS_TAG = re.compile(r"^ROS 2 graph only[^.]*\.\s+")


def first_sentence(text, limit=BRIEF_SENTENCE_MAX):
    """The first sentence of a tool description, on one line and at most `limit` characters.

    A leading "ROS 2 graph only..." lane tag is turned into a `(ROS 2)` prefix so the
    sentence that follows is the one shown. A sentence over `limit` is cut at its first colon
    or semicolon, else at `limit`.
    """
    flat = " ".join((text or "").split())
    prefix = ""
    if _ROS_TAG.match(flat):
        flat, prefix = _ROS_TAG.sub("", flat, count=1), "(ROS 2) "
    match = _SENTENCE_END.search(flat)
    sentence = flat[: match.end()] if match else flat
    if len(sentence) + len(prefix) > limit:
        cuts = [i for i in (sentence.find(": "), sentence.find("; ")) if i >= BRIEF_CUT_MIN]
        cut = min(cuts, default=-1)
        if cut >= 0 and cut + len(prefix) <= limit:
            sentence = sentence[:cut] + "."
        else:
            sentence = sentence[: limit - len(prefix) - 3].rstrip() + "..."
    return prefix + sentence


def input_names(schema):
    """Input names of a tool schema, in order, with `*` after a required one."""
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    return [f"{name}*" if name in required else name for name in props]


def brief_line(tool):
    """`name(input, input*): first sentence` for one entry of the broker's tool list."""
    inputs = ", ".join(input_names(tool.get("inputSchema")))
    return f"{tool['name']}({inputs}): {first_sentence(tool.get('description'))}"


def format_brief(tools):
    """The whole catalogue, one line per tool, with a header saying what `*` means."""
    lines = [brief_line(t) for t in tools]
    header = f"{len(lines)} tools, name(inputs): first sentence. * = required input."
    return "\n".join([header, *lines])


def parse_args(argv):
    """`(host, port, request, brief)` from the command line; raises SystemExit on misuse."""
    args = list(argv)
    host = "127.0.0.1"
    if args and args[0] == "--host":
        if len(args) < 2:
            raise SystemExit("--host needs a value")
        host, args = args[1], args[2:]
    if len(args) < 2:
        raise SystemExit(__doc__)
    port, what = int(args[0]), args[1]
    rest = args[2:]
    if what == "list":
        only = rest[1] if len(rest) == 2 and rest[0] == "--tool" else None
        if rest and rest != ["--brief"] and only is None:
            raise SystemExit("usage: tf.py <port> list [--brief | --tool <name>]")
        return host, port, {"list": True}, ("brief" if rest == ["--brief"] else only or "")
    return host, port, {"tool": what, "args": json.loads(rest[0]) if rest else {}}, ""


def ask(host, port, req):
    """Send one request to the broker and return its decoded answer."""
    with socket.create_connection((host, port), timeout=120) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return json.loads(data)


def main(argv):
    host, port, req, view = parse_args(argv)
    answer = ask(host, port, req)
    if req.get("list") and view == "brief":
        print(format_brief(answer))
    elif req.get("list") and view:
        match = [t for t in answer if t["name"] == view]
        if not match:
            raise SystemExit(f"no tool named {view!r}")
        print(json.dumps(match[0], indent=1))
    else:
        print(json.dumps(answer, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
