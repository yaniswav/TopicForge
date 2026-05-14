# DDS Interoperability — what TopicForge observes

TopicForge does not implement the DDS-RTPS wire protocol from scratch. It joins a DDS domain as a read-only participant (via the [Eclipse CycloneDDS](https://cyclonedds.io) or [eProsima Fast DDS](https://fast-dds.docs.eprosima.com/) Python bindings, your choice) and reports what it sees on the bus.

The OMG-DDS-RTPS standard guarantees that all conformant implementations interoperate. This means **TopicForge sees publishers and subscribers from any vendor — including those written in C, C++11, C++17, Rust, Java, .NET, or any other language with DDS-RTPS bindings** — as long as they share the same DDS domain.

## OMG-validated interoperability (May 2025)

The [OMG DDS Interoperability Test](https://omg-dds.github.io/dds-rtps/test_results.html) ran 47 conformance tests across every pub/sub pair of six implementations. Summary:

| Implementation | Version | Language | License | Mostly interops with |
| --- | --- | --- | --- | --- |
| RTI Connext DDS | 6.1.2 | C++ | Commercial | All 5 other vendors (47/47 per pair) |
| eProsima Fast DDS | 3.1.0 | C++ | Apache 2.0 | All 5 other vendors |
| InterCOM DDS | 3.16.2.0 | C++ | Commercial | All 5 other vendors |
| OpenDDS | 3.32.0 | C++ | Apache-style | 4 vendors fully ; Dust DDS partial |
| CoreDX DDS | 6.0.0 | C++ | Commercial | All 5 other vendors |
| Dust DDS | 0.11.0 | Rust | Apache 2.0 | 4 vendors fully ; OpenDDS partial |

Notice: a Rust implementation is in this list. That is the OMG-DDS promise — language is irrelevant at the wire level.

[CycloneDDS](https://cyclonedds.io) (Eclipse Foundation, BSD) is not part of this particular OMG report cycle but is a long-standing DDS-RTPS conformant implementation, validated against the other vendors through the Eclipse community test programs.

## What this means for TopicForge

When you install TopicForge with DDS support (`pip install topicforge[dds]`), it brings in one of the two OSS Python participants — CycloneDDS or Fast DDS — and joins the domain you point it at.

From there, TopicForge's MCP tools (`list_participants`, `list_topics`, `detect_qos_mismatches`, `peek_dds_samples`) see **every conformant participant on the bus**, regardless of:

- **The vendor** — RTI Connext, OpenDDS, CoreDX, Fast DDS, Cyclone, InterCOM, Dust DDS, or any other DDS-RTPS conformant stack
- **The host language** — C, C++11/14/17/20, Rust, Java, .NET, Python, Ada, anything with a binding
- **The version** — different versions of the same vendor coexist on the bus as the standard intends

The two known interop gaps from the 2025-05 OMG report (Dust DDS ↔ OpenDDS, Dust DDS ↔ CoreDX) live at the application layer between those specific implementations — they are not gaps in what TopicForge can observe. TopicForge's participant still discovers the other endpoints; it just reports what is on the bus.

## What TopicForge is not

- **It is not a DDS implementation.** It joins as a read-only client using a conformant vendor's participant SDK.
- **It is not vendor-locked.** Your bus can run RTI Connext, Fast DDS, OpenDDS, anything. TopicForge sees them.
- **It does not publish.** Ever. Architecturally. There is no write path — this is the safety contract documented in `docs/product-plan.md §1`.
- **It is not certified.** Certification (DO-178C, ISO 26262 ASIL, etc.) is the responsibility of the deployment environment. TopicForge being read-only narrows the certification scope but does not eliminate it.

## References

- [OMG DDS Foundation — the standard](https://www.dds-foundation.org/omg-dds-standard/)
- [OMG DDS-RTPS interoperability test description](https://omg-dds.github.io/dds-rtps/test_description.html)
- [OMG DDS-RTPS interoperability test results (current)](https://omg-dds.github.io/dds-rtps/test_results.html)
- [Source data archived in this repo](projet-file/references/omg-dds-interop-2025-05-08.xlsx) (internal copy of the 2025-05-08 snapshot used as reference for TopicForge v0.3.0)

The TopicForge team does not run the OMG interop tests itself; the results above are the OMG Foundation's published artifact. We track the test results page and update this document at each major release if the matrix shifts materially.
