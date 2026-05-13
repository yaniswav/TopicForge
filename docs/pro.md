---
layout: default
title: TopicForge Pro — coming July 2026
description: Production-grade robotics workflows for AI agents — read-only by design.
---

# TopicForge Pro — coming July 2026

### Production-grade robotics workflows for AI agents — read-only by design.

TopicForge (open source, MIT) gives AI agents a grounded view of your ROS2 stack today. **TopicForge Pro** adds the deeper, harder-to-build tools robotics teams actually pay for — without ever giving an LLM a write path to your robot.

---

## What's coming in Pro

### 🛠️ URDF Inspector

Parse `.urdf` and `.xacro` files, return a structured view of links, joints, inertias, collision geometry, and common failure modes (zero inertias, self-collisions, broken `mesh://` paths, dangling parents). Lets your AI agent reason about a robot's kinematics before it touches a controller.

### 📉 Bag Anomaly Detector

Statistical + rule-based scan of `.mcap` / `.db3` recordings: clock jumps, frame drops, TF tree breaks, frequency drift, stale transforms, sensor desync. Returns a ranked list of anomalies with `(timestamp, severity, topic, evidence)` — the kind of report you'd ask a junior engineer to produce after a failed run.

### 🔀 Multi-bag Diff

Compare two recordings from the same scenario (before/after a code change, sim vs real, two hardware revisions) and surface meaningful deltas: missing topics, frequency changes, payload shape drift, trajectory divergence. The diff most teams currently produce with throwaway Python scripts, exposed as a single MCP tool.

All three are **read-only**. TopicForge will not ship a write path to a real robot in Pro. Safety, trust, and liability win over convenience here.

---

## Pricing

> **Early access: $12/mo, locked in for life** for the **first 10 customers**.
> After that: **$19/mo**.
> Cancel anytime. No payment is collected today.

---

## Reserve an early access slot

**0 / 10 slots claimed.**

To reserve a slot, send a one-line email — your name, your team, the robot stack you're running. That's it.

<p>
  <a
    href="mailto:ethvignot.yanis@gmail.com?subject=TopicForge%20Pro%20%E2%80%94%20early%20access%20slot&body=Hi%20Yanis%2C%0A%0AI%27d%20like%20to%20reserve%20one%20of%20the%2010%20TopicForge%20Pro%20early%20access%20slots.%0A%0AName%3A%0ATeam%2Forg%3A%0ARobot%20stack%20%28ROS2%20distro%2C%20hardware%2C%20sim%29%3A%0AMost%20painful%20introspection%20task%20today%3A%0A%0AThanks%21"
    style="display:inline-block;padding:12px 24px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;"
  >
    Reserve my slot →
  </a>
</p>

Prefer a form? A Tally embed will land here before the public launch.

---

## Honest disclaimer

I'm building this in the open and on a sane schedule. So the deal is simple:

- **No Pro feature ships until 10+ teams sign up.** If demand isn't there, the open-source MVP stays the product and you owe me nothing.
- **No payment is collected until at least one Pro feature is in your hands.** Reserving a slot is a non-binding intent, not a charge.
- **The $12/mo lifetime rate is honored for everyone in the first 10**, even if a feature slips by a month.

If those terms don't fit your purchasing process, [let's talk](mailto:ethvignot.yanis@gmail.com?subject=TopicForge%20Pro%20%E2%80%94%20procurement%20question) before signing up — happy to issue an annual invoice or work through your vendor onboarding.

---

## In the meantime

- **Use the MVP.** TopicForge v0.1.1 is shipping on PyPI. Mock mode runs without ROS2; live mode wraps the `ros2` CLI. [GitHub repo](https://github.com/yaniswav/TopicForge).
- **Read the roadmap.** Full strategy and phase plan in [`docs/product-plan.md`](https://github.com/yaniswav/TopicForge/blob/main/docs/product-plan.md).
- **File an issue** for any Pro tool you'd actually use — input shapes the build order.

---

<sub>TopicForge is built by Yanis ETHVIGNOT. The open-source MVP is MIT-licensed. Pro features will be distributed under a separate commercial license — terms published when the first Pro feature ships.</sub>
