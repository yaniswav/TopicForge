# Running the demo across a Windows and a Linux machine

Real deployments often mix Windows and Linux on one DDS domain. Discovery
normally relies on multicast; between two machines it may be blocked (WSL in
NAT mode, Docker Desktop, some Wi-Fi networks, the Windows firewall). The fix
is to give each participant the address of the other machine as a unicast
discovery peer. Multicast keeps working where it can.

## Recommended layout

Two physical machines on the same wired network. Put the observer
(TopicForge) and the Rust / Dust node on the Linux machine: **Dust has no
unicast peer setting** and discovers others only by multicast, so it should
live next to the observer.

| Machine | Participants |
|---|---|
| Linux | TopicForge (observer), Rust / Dust, C / OpenSplice |
| Windows | Python / Cyclone, C++ / Fast DDS, Python / RTI |

The Fast DDS writer on Windows and the RTI reader on Windows still form the
`DemoImu` mismatch, seen from the Linux observer across the network: that is
the industrial case.

## Per vendor

| Vendor | How to add a peer |
|---|---|
| Cyclone (and TopicForge) | `CYCLONEDDS_URI` pointing at `cyclonedds-peers.xml` |
| Fast DDS | `FASTDDS_DEFAULT_PROFILES_FILE` pointing at `fastdds-peers.xml` |
| RTI Connext | `NDDS_DISCOVERY_PEERS=udpv4://OTHER_MACHINE_IP,udpv4://239.255.0.1` (keep the multicast address or RTI stops listening to multicast) |
| OpenSplice | `DDSI2Service/Discovery/Peers/Peer@Address` in the file named by `OSPL_URI` |
| Dust DDS | none: multicast only |

## Windows firewall

Run `scripts\integration\launch\setup.ps1 -Firewall` from an administrator
PowerShell. It allows inbound UDP 7400-7500 for the demo programs on private
networks only.

## WSL as the Linux side

WSL in the default NAT mode does not pass multicast between Windows and the
distribution. Mirrored mode (`networkingMode=mirrored` in `.wslconfig`) is
documented as supporting it but is reported not to work reliably
(microsoft/WSL#12344). Use unicast peers on every vendor that supports them,
and keep Dust and the observer on the same side. A second physical machine is
more reliable than WSL for this scenario.

## Not supported

Docker Desktop on Windows: its host networking does not pass multicast
between the host and containers, and running Linux containers proves nothing
about Windows.
