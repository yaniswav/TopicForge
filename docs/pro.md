---
layout: default
title: TopicForge: commercial support and integration
description: What the free tier already covers through multi-vendor DDS-RTPS observation, what actually requires the RTI Connext binding, and how to reach out for integration or support work.
---

# TopicForge: commercial support and integration

TopicForge (open source, MIT) is read-only by architecture: there is no write path to a robot or a DDS bus, on any tier. This page is for teams that need something past what the open-source package already does - integration work, native RTI Connext access, or a support arrangement. There is no separate paid product today: no published price, no license key gate, no self-serve checkout. Everything below is arranged directly, by email.

---

## What the free tier already does

`pip install topicforge[dds]` gets you the community DDS adapters (Eclipse CycloneDDS, eProsima Fast DDS). Either one joins a DDS domain as a read-only DDS-RTPS participant, and the OMG DDS-RTPS interoperability guarantee means that participant discovers and reports every conformant vendor on the domain through the builtin discovery topics (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`) - RTI Connext included. If your goal is to *observe* what is on the bus (participants, topics, QoS mismatches, samples, lifecycle events, metrics), the free tier already does that against an RTI-based system: you do not need RTI's own SDK, and you do not need a license, to see an RTI publisher from a Cyclone or Fast DDS participant. See [`dds-interop-matrix.md`](dds-interop-matrix.md) for how that works and its known gaps.

The ROS2 graph introspection tools are plain OSS as well and unaffected by anything below.

If that covers your use case, you most likely need nothing on this page.

---

## When you need the RTI Connext adapter specifically

Observing an RTI bus from a Cyclone or Fast DDS participant covers the common case, but it is still a foreign participant on that bus: RTI-proprietary transports and security modes that never touch standard RTPS discovery stay invisible to it. Concretely, reach for the native `topicforge_pro` RTI Connext adapter instead of the free path when your domain runs RTI's own DDS Security plugin with credentials, uses a pure shared-memory transport, or depends on RTI-only proprietary extensions.

That adapter exists and works, but it is bring-your-own-everything: you supply the `rti.connextdds` Python binding and a valid RTI Connext DDS license yourself. TopicForge does not bundle, resell, or otherwise redistribute either. Email if this is your situation and we will work out the setup together.

No other commercial DDS vendor adapter is implemented today. A couple of other vendor slots exist in the `topicforge_pro` package layout for future work, but they carry no working code - do not plan around them.

---

## What's available on request

- Integration and adaptation to a specific ROS2 / DDS environment.
- Native RTI Connext adapter setup, as described above.
- A support arrangement for teams running TopicForge in production.

None of this has a published price. Email with what you need and it gets scoped from there.

---

## Known limitations

- DDS Security is not implemented on any adapter, free or otherwise. If your domain requires authenticated or encrypted RTPS, TopicForge cannot join it today.
- `detect_qos_mismatches` covers four QoS policies: Reliability, Durability, History, and Deadline. Liveliness, Ownership, and Partition are not checked.

---

## Contact

<p>
  <a
    href="mailto:ethvignot.yanis@gmail.com?subject=TopicForge%20-%20commercial%20support%20inquiry&body=Hi%20Yanis%2C%0A%0AI%27m%20interested%20in%20TopicForge%20commercial%20support.%0A%0AWhat%20I%20need%3A%0ADDS%20vendor%28s%29%20in%20use%3A%0AROS2%20distro%20%28if%20applicable%29%3A%0ADeployment%20environment%20%28secure%20domain%2C%20shared%20memory%2C%20other%29%3A%0A%0AThanks%21"
    style="display:inline-block;padding:12px 24px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;"
  >
    Get in touch ->
  </a>
</p>

Inbound only - there is no outbound sales process. If you are unsure whether you need anything at all, re-read the free tier section above first.

---

<sub>TopicForge is built by Yanis ETHVIGNOT. The open-source core is MIT-licensed. The RTI Connext adapter and any support arrangement described on this page are handled case by case, by direct agreement, with no published terms.</sub>
