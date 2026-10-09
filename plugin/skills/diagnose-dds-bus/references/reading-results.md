# Reading TopicForge DDS results

## MismatchScan

- `matched`: pairs DDS will connect given the announced QoS. Data flow is not observed. A pair flagged `late_joiner` is a VOLATILE writer whose reader joined later: normal, not a fault.
- `not_matched`: pairs DDS never matches (different partitions or type names). QoS rules are not evaluated for them. `latent_incompatible_policies` lists RxO policies that would also be incompatible once the partition or type issue is fixed, so tell the user about both.
- `reports`: incompatible or risky QoS pairs. `details` holds `policy`, `requested`, `offered` and the `rule`, for example a RELIABLE reader needs a RELIABLE writer. Fix direction: relax the reader or strengthen the writer. History appears only as `risky`, never as incompatible.
- `hints`: orphan topics with a near-identical name (probable typo) and type id notes.
- `policies_checked` / `policies_unchecked`: what was and was not compared. Quote `policies_unchecked` when claiming the bus is fine.
- Lists are capped at 200 entries; `truncated` is true and the `*_total` fields keep the real counts.

Checked policies: Partition (`*` and `?` wildcards), type name, Reliability, Durability, Deadline, Liveliness, LatencyBudget, Ownership, DestinationOrder, DataRepresentation, History.

## EndpointListing

- One entry per writer or reader with `role`, `topic`, `type_name`, owning participant and structured `qos`.
- In `qos`, a duration of `None` means infinite or not set; a field of `None` means the endpoint did not announce it.
- Liveness is not observed (the listing's `hints` say so once): TopicForge cannot tell a silent writer from a healthy one.
- Ownership: among EXCLUSIVE writers the live one with the highest strength delivers. Which one currently owns an instance is runtime state TopicForge cannot see.
- Departed endpoints are remembered (last 200, 1 hour). Pass `include_departed` to list them in `endpoints`.
- A topic filter that matches nothing returns a `note` with the closest known topics.

## Participants and events

- `vendor` can be `unknown` (Dust DDS, RTI Connext by default): that is a limit of GUID-prefix detection, not a fault.
- `is_observer` marks TopicForge's own participant.
- A `lost` timestamp is an upper bound of the death. After a clean shutdown it is exact; after a crash it is when the lease expired (Cyclone default 10 s, Fast DDS 20 s, RTI 100 s), and the two cannot be told apart.
- A restarted node is a new participant: one `lost` and one `discovered`, different `guid`, same `name`.
- Events cover only the time since TopicForge started watching, capped at 200 (newest first).
