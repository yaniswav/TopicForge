"""Stand-in for `rclpy.serialization`: a raw message here is its index as text."""


def serialize_index(index):
    return str(index).encode("ascii")


def deserialize_message(raw, cls):
    return cls(int(raw))
