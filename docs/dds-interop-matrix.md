# DDS Interoperability: what TopicForge observes

TopicForge does not implement the DDS-RTPS wire protocol from scratch. It joins a DDS domain as a read-only participant (via the [Eclipse CycloneDDS](https://cyclonedds.io) Python binding, or [eProsima Fast DDS](https://fast-dds.docs.eprosima.com/) with a binding built from source) and reports what it sees on the bus.

The OMG-DDS-RTPS standard guarantees that all conformant implementations interoperate. This means TopicForge sees publishers and subscribers from any vendor, including those written in C, C++11, C++17, Rust, Java, .NET, or any other language with DDS-RTPS bindings, as long as they share the same DDS domain.

## OMG-validated interoperability (May 2025)

The [OMG DDS Interoperability Test](https://omg-dds.github.io/dds-rtps/test_results.html) ran 47 conformance tests across every pub/sub pair of six implementations. Summary:

| Implementation | Version | Language | License | Mostly interops with |
| --- | --- | --- | --- | --- |
| RTI Connext DDS | 6.1.2 | C++ | Commercial | All 5 other vendors (47/47 per pair) |
| eProsima Fast DDS | 3.1.0 | C++ | Apache 2.0 | All 5 other vendors |
| InterCOM DDS | 3.16.2.0 | C++ | Commercial | All 5 other vendors |
| OpenDDS | 3.32.0 | C++ | Apache-style | 4 vendors fully; Dust DDS partial |
| CoreDX DDS | 6.0.0 | C++ | Commercial | All 5 other vendors |
| Dust DDS | 0.11.0 | Rust | Apache 2.0 | 4 vendors fully; OpenDDS partial |

One of the six is written in Rust: at the wire level the language does not matter.

[CycloneDDS](https://cyclonedds.io) (Eclipse Foundation, BSD) is not part of this particular OMG report cycle but is a long-standing DDS-RTPS conformant implementation, validated against the other vendors through the Eclipse community test programs.

## What this means for TopicForge

When you install TopicForge with DDS support (`pip install topicforge[dds]`, which installs the CycloneDDS Python binding), it joins the domain you point it at as a read-only participant. The Fast DDS adapter works the same way, but its Python binding is not on PyPI: you build it from eProsima's sources (see [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md)).

From there, TopicForge's discovery-based tools (`list_participants`, `list_endpoints`, `detect_qos_mismatches`, `participant_events`, and `peek_dds_samples` on the builtin `DCPS*` topics) see every conformant participant on the bus, regardless of:

- The vendor: RTI Connext, OpenDDS, CoreDX, Fast DDS, Cyclone, InterCOM, Dust DDS, or any other DDS-RTPS conformant stack
- The host language: C, C++11/14/17/20, Rust, Java, .NET, Python, Ada, anything with a binding
- The version: different versions of the same vendor coexist on the bus as the standard intends

The two known interop gaps from the 2025-05 OMG report (Dust DDS <-> OpenDDS, Dust DDS <-> CoreDX) live at the application layer between those specific implementations. They do not limit what TopicForge can observe: its participant still discovers the other endpoints.

## Limits of this claim

The claim is about discovery: which participants, readers and writers exist, and what QoS they announce. It does not extend to user-topic payloads: `peek_dds_samples` on a user topic returns count 0 and a note, and does not decode contents, for any vendor; `list_endpoints` shows the topic's writers, readers and QoS. The project's own real-bus runs cover Cyclone and Dust DDS participants only (see [`../scripts/integration/README.md`](../scripts/integration/README.md)); RTI, OpenDDS, CoreDX, OpenSplice and Fast DDS have not been observed. For the rest the claim follows from the RTPS standard and from the OMG's published results above. Vendors that do not follow the RTPS vendor-id convention in the GUID prefix (Dust DDS, RTI by default) are reported with vendor `unknown` on Cyclone. Domains that use DDS Security are not observable at all, because TopicForge joins without credentials.

## What TopicForge is not

TopicForge is not a DDS implementation: it joins as a read-only client using a conformant vendor's participant SDK. It is not tied to one vendor either, since your bus can run RTI Connext, Fast DDS, OpenDDS or anything else conformant. It does not publish, by architecture: there is no write path, which is the safety contract documented in `docs/product-plan.md section 1`. And it is not certified. Certification (DO-178C, ISO 26262 ASIL, etc.) is the responsibility of the deployment environment; being read-only narrows the certification scope but does not remove it.

## References

- [OMG DDS Foundation: the standard](https://www.dds-foundation.org/omg-dds-standard/)
- [OMG DDS-RTPS interoperability test description](https://omg-dds.github.io/dds-rtps/test_description.html)
- [OMG DDS-RTPS interoperability test results (current)](https://omg-dds.github.io/dds-rtps/test_results.html)
- [Source data archived in this repo](projet-file/references/omg-dds-interop-2025-05-08.xlsx) (2025-05-08 snapshot)

TopicForge does not run the OMG interop tests itself; the results above are the OMG Foundation's published artifact.

If you already run the OMG DDS interoperability demo
(https://github.com/omg-dds/dds-rtps), TopicForge sees its participants like
any others.
