"""Stand-in for `rosidl_runtime_py.message_to_yaml`: the text depends on the flags it gets."""


def message_to_yaml(msg, truncate_length=None, no_arr=False, no_str=False):
    size = 4000 if truncate_length is None else 2
    values = "'<sequence type: int32, length: 4000>'" if no_arr else f"{[0] * size}"
    return f"index: {msg.index}\nvalues: {values}\nlabel: café\n"
