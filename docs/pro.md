---
layout: default
title: TopicForge: commercial support and integration
description: What the free tier already covers, what actually requires the RTI Connext binding, and how to reach out for integration or support work.
---

# TopicForge: commercial support and integration

TopicForge (open source, MIT) is read-only by architecture: there is no write path to a robot or a DDS bus, for anyone. This page is for teams that need something past what the package already does: integration work, native RTI Connext access, or a support arrangement. There is no separate paid product, no published price, no license key and no self-serve checkout. Everything is arranged directly, by email, inbound only.

## What the free package already does

`pip install topicforge[dds]` installs the Eclipse CycloneDDS adapter. It joins a DDS domain as a read-only RTPS participant and, because the OMG DDS-RTPS discovery protocol is standardized, reports every conformant vendor on the domain through the builtin discovery topics, RTI Connext included: participants, readers and writers, their QoS, QoS mismatches, and lifecycle events. You need neither RTI's SDK nor a license to see an RTI publisher. This has been exercised against Cyclone and Dust DDS participants; RTI itself has not been observed yet. See [`dds-interop-matrix.md`](dds-interop-matrix.md). The ROS2 graph and bag tools are plain open source too. If that covers your use case, you need nothing on this page.

## When the RTI Connext adapter is the right tool

An observer from another vendor is still a foreign participant: RTI-proprietary transports and security modes that never touch standard RTPS discovery stay invisible to it. A native RTI Connext adapter exists for the cases where your domain runs RTI's DDS Security plugin with credentials, uses a pure shared-memory transport, or depends on RTI-only extensions. It is bring-your-own-everything: you supply the `rti.connextdds` Python binding and a valid RTI Connext license; TopicForge does not bundle, resell or redistribute either. The open-source core does not load it automatically, and `TOPICFORGE_DDS_BACKEND=rti` is rejected at startup; how the adapter is delivered and run is part of the engagement. No other commercial vendor adapter has working code, so do not plan around one.

## Available on request

Integration and adaptation to a specific ROS2 / DDS environment, native RTI Connext adapter setup, and a support arrangement for teams running TopicForge in production. Candidate deliverables beyond the current tool surface (URDF inspection, bag anomaly detection, multi-bag diff) are scoped the same way. None of this has a published price: email what you need and it gets scoped from there.

## Known limitations

DDS Security is not implemented on any adapter: if your domain requires authenticated or encrypted RTPS, TopicForge cannot join it today. `detect_qos_mismatches` covers Partition, type name, Reliability, Durability, Deadline, Liveliness, LatencyBudget, Ownership (kind), DestinationOrder and DataRepresentation, with History as a risk; Presentation, XTypes assignability and runtime behavior are not checked.

## Contact

<p>
  <a
    href="mailto:ethvignot.yanis@gmail.com?subject=TopicForge%20-%20commercial%20support%20inquiry&body=Hi%20Yanis%2C%0A%0AI%27m%20interested%20in%20TopicForge%20commercial%20support.%0A%0AWhat%20I%20need%3A%0ADDS%20vendor%28s%29%20in%20use%3A%0AROS2%20distro%20%28if%20applicable%29%3A%0ADeployment%20environment%20%28secure%20domain%2C%20shared%20memory%2C%20other%29%3A%0A%0AThanks%21"
    style="display:inline-block;padding:12px 24px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;"
  >
    Get in touch ->
  </a>
</p>

<sub>Built by Yanis ETHVIGNOT. The open-source core is MIT-licensed; the RTI Connext adapter and any support arrangement are handled case by case, by direct agreement.</sub>
