"""Ask TopicForge a question through the broker.

python tf.py <port> list                       # the tools and their descriptions
python tf.py <port> <tool> '<json arguments>'  # call one tool
"""

import json
import socket
import sys

port = int(sys.argv[1])
req = (
    {"list": True}
    if sys.argv[2] == "list"
    else {"tool": sys.argv[2], "args": json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}}
)
with socket.create_connection(("127.0.0.1", port), timeout=120) as s:
    s.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while chunk := s.recv(65536):
        data += chunk
print(json.dumps(json.loads(data), indent=1))
