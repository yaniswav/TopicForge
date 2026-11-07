"""Stand-in for `rosidl_runtime_py.utilities.get_message`."""


class FakeMessage:
    def __init__(self, index=0):
        self.index = index


def get_message(identifier):
    if identifier.startswith("missing_pkg/"):
        raise ModuleNotFoundError(f"No module named '{identifier.split('/')[0]}'")
    return FakeMessage
