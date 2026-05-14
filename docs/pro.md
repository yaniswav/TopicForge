---
layout: default
title: TopicForge Pro — coming July 2026
description: Commercial DDS adapters + advanced diagnostics for AI-driven robotics — read-only by design.
---

# TopicForge Pro — coming July 2026

### Commercial DDS adapters + advanced diagnostics for AI-driven robotics — read-only by design.

TopicForge (open source, MIT) covers the **community DDS adapters** (Eclipse CycloneDDS, eProsima Fast DDS — plus OpenDDS / Dust DDS stubs as their Python bindings mature) and the ROS2 graph introspection surface. **TopicForge Pro** adds the **commercial DDS adapters** that defense, aerospace, automotive, and naval teams actually deploy in production, plus advanced diagnostic features — without ever giving an LLM a write path to your bus.

---

## What's coming in Pro

### 🏛️ Commercial DDS adapters

The OSS core uses the OMG-DDS-RTPS protocol guarantee to *observe* every vendor on the bus — but to *join* the bus on top of a commercial stack you need that vendor's own SDK and license. Pro ships TopicForge-shaped adapters for the four commercial DDS implementations from the [OMG May 2025 interop matrix](dds-interop-matrix.md):

| Adapter            | Vendor                       | License                       | Status            |
| ------------------ | ---------------------------- | ----------------------------- | ----------------- |
| **RTI Connext**    | Real-Time Innovations        | BYO RTI Connext DDS license   | Phase 2 (priority)|
| **OpenSplice**     | ADLink / EOL                 | BYO (legacy support only)     | Stub — see notes  |
| **CoreDX DDS**     | Twin Oaks Computing          | BYO CoreDX license            | Phase 2+          |
| **InterCOM DDS**   | TechSoft (Gurum line)        | BYO InterCOM license          | Phase 2+          |

Each adapter joins the bus as a **read-only DDS-RTPS participant** using its vendor's own native bindings — and like the OSS adapters, observes every conformant participant on the domain regardless of vendor. The same nine MCP tools (`health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`) work identically across all four commercial backends and the four OSS backends. **No write path on any adapter.**

### 🛠️ URDF Inspector

Parse `.urdf` and `.xacro` files, return a structured view of links, joints, inertias, collision geometry, and common failure modes (zero inertias, self-collisions, broken `mesh://` paths, dangling parents). Lets your AI agent reason about a robot's kinematics before it touches a controller.

### 📉 Bag Anomaly Detector

Statistical + rule-based scan of `.mcap` / `.db3` recordings: clock jumps, frame drops, TF tree breaks, frequency drift, stale transforms, sensor desync. Returns a ranked list of anomalies with `(timestamp, severity, topic, evidence)` — the kind of report you'd ask a junior engineer to produce after a failed run.

### 🔀 Multi-bag Diff

Compare two recordings from the same scenario (before/after a code change, sim vs real, two hardware revisions) and surface meaningful deltas: missing topics, frequency changes, payload shape drift, trajectory divergence. The diff most teams currently produce with throwaway Python scripts, exposed as a single MCP tool.

All features are **read-only**. TopicForge Pro will not ship a write path to a real robot, ever. Safety, trust, and liability win over convenience here — and the `MiddlewareAdapter` protocol shape physically forbids a write method.

---

## OSS vs Pro at a glance

| Capability                          | OSS (`pip install topicforge`)                    | Pro (`pip install topicforge-pro`)              |
| ----------------------------------- | ------------------------------------------------- | ----------------------------------------------- |
| ROS2 graph introspection (5 tools)  | ✓                                                 | ✓                                               |
| Bare DDS observability (4 tools)    | ✓ (Cyclone, Fast — OpenDDS / Dust stubs)          | ✓ (RTI Connext, OpenSplice, CoreDX, InterCOM)   |
| Auto-detect installed SDK            | ✓ (8-vendor priority chain)                       | ✓ (Pro vendors get priority over OSS)           |
| Composite adapter (ROS + DDS)        | ✓                                                 | ✓                                               |
| Read-only by architecture            | ✓                                                 | ✓                                               |
| URDF Inspector                       | —                                                 | ✓                                               |
| Bag Anomaly Detector                 | —                                                 | ✓                                               |
| Multi-bag Diff                       | —                                                 | ✓                                               |
| License                              | MIT                                               | Commercial (per-seat or annual)                 |
| Vendor SDK                           | OSS Python bindings (BSD / Apache)                | BYO commercial license per vendor               |

Pro and OSS install side-by-side. The OSS core never imports a Pro adapter directly ; the Pro plugin registers itself via the `_try_register_pro(mcp)` hook in `server/app.py` at startup. Uninstall `topicforge-pro` and the server keeps working with the OSS surface only — no half-broken intermediate state.

---

## Pricing

> **Early access: $12/mo, locked in for life** for the **first 10 customers**.
> After that: **$19/mo**.
> Cancel anytime. No payment is collected today.

The early-access rate covers the full Pro feature set (commercial DDS adapters + URDF / Bag Anomaly / Multi-bag Diff) as features ship. You bring your own vendor license for whichever commercial DDS stack you operate (RTI, OpenSplice, etc.) — TopicForge Pro does not bundle or redistribute vendor SDKs.

---

## Reserve an early access slot

**0 / 10 slots claimed.**

To reserve a slot, send a one-line email — your name, your team, the DDS stack you're running. That's it.

<p>
  <a
    href="mailto:ethvignot.yanis@gmail.com?subject=TopicForge%20Pro%20%E2%80%94%20early%20access%20slot&body=Hi%20Yanis%2C%0A%0AI%27d%20like%20to%20reserve%20one%20of%20the%2010%20TopicForge%20Pro%20early%20access%20slots.%0A%0AName%3A%0ATeam%2Forg%3A%0ADDS%20stack%20%28vendor%2C%20license%20held%2C%20deployment%20environment%29%3A%0AROS2%20distro%20%28if%20applicable%29%3A%0AMost%20painful%20diagnostic%20task%20today%3A%0A%0AThanks%21"
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
- **OpenSplice is EOL upstream** and shipped as a stub for legacy support only — the priority Pro deliverable is RTI Connext, with CoreDX and InterCOM following based on customer demand.

If those terms don't fit your purchasing process, [let's talk](mailto:ethvignot.yanis@gmail.com?subject=TopicForge%20Pro%20%E2%80%94%20procurement%20question) before signing up — happy to issue an annual invoice or work through your vendor onboarding.

---

## In the meantime

- **Use the MVP.** TopicForge v0.3.0+ is shipping on PyPI. Mock mode runs without ROS2 or DDS ; install `topicforge[dds]` to get the two OSS DDS adapters and the multi-vendor wire observability they unlock via the OMG-DDS-RTPS protocol. [GitHub repo](https://github.com/yaniswav/TopicForge).
- **Read the roadmap.** Full strategy and phase plan in [`docs/product-plan.md`](https://github.com/yaniswav/TopicForge/blob/main/docs/product-plan.md).
- **File an issue** for any Pro adapter or diagnostic feature you'd actually use — input shapes the build order.

---

<sub>TopicForge is built by Yanis ETHVIGNOT. The open-source MVP is MIT-licensed and covers the community DDS adapters (Cyclone, Fast — OpenDDS / Dust stubs). Pro features (commercial DDS adapters + advanced diagnostics) will be distributed under a separate commercial license — terms published when the first Pro feature ships.</sub>
