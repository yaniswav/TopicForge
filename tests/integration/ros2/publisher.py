"""Publisher node that mimics a simulated 2D-lidar robot (runs inside the bench container).

Everything is stamped with simulated time that starts at 0, and `/clock`
is published by this same node. The numbers it publishes come from `ground_truth.py`, which
also writes the matching `ground_truth.json` when the node starts.
"""

from __future__ import annotations

import math
import time

import ground_truth as model
import rclpy
from action_msgs.msg import GoalStatusArray
from bond.msg import Status as BondStatus
from builtin_interfaces.msg import Time
from example_interfaces.action import Fibonacci
from geometry_msgs.msg import PoseStamped, Transform, TransformStamped, Twist
from ground_truth import CAMERA_DELAY_SEC, N_BEAMS
from rclpy.action import ActionServer
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, Imu, LaserScan
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage


def sim_time_msg(t: float) -> Time:
    sec = int(t)
    return Time(sec=sec, nanosec=int((t - sec) * 1e9))


class BenchRobot(Node):
    def __init__(self) -> None:
        super().__init__("bench_robot")
        self._t0 = time.monotonic()
        qos = QoSProfile(depth=10)
        self._clock_pub = self.create_publisher(Clock, "/clock", qos)
        self._scan_pub = self.create_publisher(LaserScan, "/scan", qos)
        self._twist_pub = self.create_publisher(Twist, "/cmd_vel_out", qos)
        self._image_pub = self.create_publisher(Image, "/camera/image_raw", qos)
        self._edge_pub = self.create_publisher(LaserScan, "/scan_edge", qos)
        latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._desc_pub = self.create_publisher(String, "/robot_description_lite", latched)
        # tf2's own QoS: /tf is reliable and volatile with a deep queue, /tf_static latched.
        self._tf_pub = self.create_publisher(TFMessage, "/tf", QoSProfile(depth=100))
        self._tf_static_pub = self.create_publisher(TFMessage, "/tf_static", latched)
        self._tf_subscribers = 0
        self._tf_armed_at = 0.0

        self._image_data = bytes(640 * 480 * 3)
        self.create_timer(model.CLOCK_PERIOD, self._tick_clock)
        self.create_timer(model.SCAN_PERIOD, self._tick_scan)
        self.create_timer(model.TWIST_PERIOD, self._tick_twist)
        self.create_timer(model.IMAGE_PERIOD, self._tick_image)
        self.create_timer(model.EDGE_PERIOD, self._tick_edge)
        self.create_timer(model.TF_PERIOD, self._tick_tf)

        desc = String()
        desc.data = "bench_robot: differential drive, 2D lidar"
        self._desc_pub.publish(desc)
        self._tf_static_pub.publish(
            TFMessage(transforms=[self._transform(p, c, 0.0) for p, c in model.TF_STATIC_EDGES])
        )
        self._setup_clock_topics(qos)
        self._setup_nav_primitives(qos, latched)
        # /rosout lines for the log filter bench (truth: `ground_truth.logs_truth`).
        self.create_timer(model.LOG_WARN_PERIOD, self._log_warn)
        self.create_timer(model.LOG_ERROR_PERIOD, self._log_error)

    # ---- clock topics (0.7.3): wall-stamped, mixed-clock and regressing stamps
    def _setup_clock_topics(self, qos: QoSProfile) -> None:
        """Topics whose stamps are on a known clock; truth in `ground_truth.clock_truth`."""
        self._imu_pub = self.create_publisher(Imu, "/imu/data", qos)
        self._pose_sim_pub = self.create_publisher(PoseStamped, "/pose_mixed", qos)
        self._pose_wall_pub = self.create_publisher(PoseStamped, "/pose_mixed", qos)
        self._regress_pub = self.create_publisher(PoseStamped, "/stamps_regress", qos)
        self._regress_count = 0
        self.create_timer(model.IMU_PERIOD, self._tick_imu)
        self.create_timer(model.POSE_PERIOD, self._tick_pose_mixed)
        self.create_timer(model.REGRESS_PERIOD, self._tick_regress)

    # ---- Nav2 primitives (0.8.0): truth in `ground_truth.nav_primitives_truth`
    def _setup_nav_primitives(self, qos: QoSProfile, latched: QoSProfile) -> None:
        """`/bond` heartbeats and an action server nothing ever calls.

        An idle `ActionServer` publishes nothing on its `_action/status` topic until a goal
        changes state, so one empty `GoalStatusArray` is published by hand with the status
        topic's own QoS (reliable, transient local, depth 1): a sampler then finds the latched
        message a Nav2 server has after its first goal. The topic is hidden from `ros2 topic list`.
        """
        self._bond_pub = self.create_publisher(BondStatus, model.BOND_TOPIC, qos)
        self.create_timer(model.BOND_PERIOD, self._tick_bond)
        self._fib_server = ActionServer(self, Fibonacci, model.ACTION_NAME, self._execute_fib)
        self._status_pub = self.create_publisher(
            GoalStatusArray, model.ACTION_STATUS_TOPIC, latched
        )
        self._status_pub.publish(GoalStatusArray())

    def _execute_fib(self, goal_handle) -> Fibonacci.Result:
        """Never reached: no client sends a goal. Aborts it if one ever does."""
        goal_handle.abort()
        return Fibonacci.Result()

    def _tick_bond(self) -> None:
        msg = BondStatus()
        msg.header.stamp = sim_time_msg(self._now())
        msg.id = model.BOND_ID
        msg.instance_id = "bench_instance"
        msg.active = True
        msg.heartbeat_timeout = 4.0
        msg.heartbeat_period = float(model.BOND_PERIOD)
        self._bond_pub.publish(msg)

    @staticmethod
    def _wall_time_msg(age_s: float = 0.0) -> Time:
        return sim_time_msg(time.time() - age_s)

    def _log_warn(self) -> None:
        self.get_logger().warning("bench warning")

    def _log_error(self) -> None:
        self.get_logger().error("bench error")

    def _tick_imu(self) -> None:
        msg = Imu()
        msg.header.stamp = self._wall_time_msg(model.IMU_WALL_AGE_S)
        msg.header.frame_id = "imu_link"
        msg.orientation.w = 1.0
        self._imu_pub.publish(msg)

    def _tick_pose_mixed(self) -> None:
        sim, wall = PoseStamped(), PoseStamped()
        sim.header.stamp = sim_time_msg(self._now())
        wall.header.stamp = self._wall_time_msg()
        self._pose_sim_pub.publish(sim)
        self._pose_wall_pub.publish(wall)

    def _tick_regress(self) -> None:
        """Simulated stamps, every `REGRESS_EVERY`th one `REGRESS_DIP_S` behind."""
        self._regress_count += 1
        stamp = self._now()
        if self._regress_count % model.REGRESS_EVERY == 0:
            stamp = max(stamp - model.REGRESS_DIP_S, 0.001)
        msg = PoseStamped()
        msg.header.stamp = sim_time_msg(stamp)
        self._regress_pub.publish(msg)

    @staticmethod
    def _transform(parent: str, child: str, t: float) -> TransformStamped:
        msg = TransformStamped()
        msg.header.stamp = sim_time_msg(t)
        msg.header.frame_id = parent
        msg.child_frame_id = child
        msg.transform = Transform()
        msg.transform.translation.x = 1.0
        msg.transform.rotation.w = 1.0
        return msg

    def _tick_tf(self) -> None:
        """Steady edges always; the flaky edge for a few seconds after each new subscriber."""
        now = self._now()
        subscribers = self._tf_pub.get_subscription_count()
        if subscribers > 0 and self._tf_subscribers == 0:
            self._tf_armed_at = now
        self._tf_subscribers = subscribers
        edges = list(model.TF_STEADY_EDGES)
        if subscribers > 0 and now - self._tf_armed_at < model.TF_FLAKY_ACTIVE_S:
            edges.append(model.TF_FLAKY_EDGE)
        self._tf_pub.publish(TFMessage(transforms=[self._transform(p, c, now) for p, c in edges]))

    def _now(self) -> float:
        return time.monotonic() - self._t0

    def _tick_clock(self) -> None:
        msg = Clock()
        msg.clock = sim_time_msg(self._now())
        self._clock_pub.publish(msg)

    def _tick_scan(self) -> None:
        msg = LaserScan()
        msg.header.stamp = sim_time_msg(self._now())
        msg.header.frame_id = model.SCAN_FRAME
        msg.angle_min = model.ANGLE_MIN
        msg.angle_max = -model.ANGLE_MIN
        msg.angle_increment = model.ANGLE_INCREMENT
        msg.time_increment = 0.0
        msg.scan_time = model.SCAN_TIME
        msg.range_min = model.RANGE_MIN
        msg.range_max = model.RANGE_MAX
        msg.ranges = model.scan_ranges()
        msg.intensities = [float(i) for i in range(N_BEAMS)]
        self._scan_pub.publish(msg)

    def _tick_edge(self) -> None:
        # No-return beams: lidar drivers publish `inf`, and `nan` for invalid ones.
        msg = LaserScan()
        msg.header.stamp = sim_time_msg(self._now())
        msg.header.frame_id = "edge_laser"
        msg.ranges = [math.inf, math.nan, -math.inf, 1.5]
        self._edge_pub.publish(msg)

    def _tick_twist(self) -> None:
        msg = Twist()
        msg.linear.x = 0.25
        msg.angular.z = 0.5
        self._twist_pub.publish(msg)

    def _tick_image(self) -> None:
        if self._now() < CAMERA_DELAY_SEC:
            return
        msg = Image()
        msg.header.stamp = sim_time_msg(self._now())
        msg.header.frame_id = "camera_link"
        msg.height = 480
        msg.width = 640
        msg.encoding = "rgb8"
        msg.step = 640 * 3
        msg.data = self._image_data
        self._image_pub.publish(msg)


def main() -> None:
    model.write_truth()
    rclpy.init()
    node = BenchRobot()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
