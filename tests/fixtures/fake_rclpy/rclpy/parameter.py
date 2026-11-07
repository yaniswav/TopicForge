"""Stand-in for `rclpy.parameter`."""


class Parameter:
    def __init__(self, name, value=None):
        self.name = name
        self.value = value
