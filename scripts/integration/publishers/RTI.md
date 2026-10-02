# RTI Connext participant (demo)

`rti_publisher.py` is the RTI Connext node of the multi-vendor demo. It runs
locally only. It needs the RTI Connext 7.x Python API and a license file.

## Topics

Same contract as `../DEMO_CONTRACT.md`, plus one topic that belongs to this node:

| Topic | Type (IDL) | Writer | Reader | Expected |
|---|---|---|---|---|
| `DemoImu` | `struct Imu { uint32 seq; double yaw_rad; };` | none (Fast DDS writes it) | Python / RTI Connext, RELIABLE | Reliability mismatch against the BEST_EFFORT Fast DDS writer |
| `DemoHeartbeat` | `struct Heartbeat { uint32 seq; };` | Python / RTI Connext, RELIABLE, 1 Hz | none | Visible publication only |

TopicForge may show this participant's vendor as `unknown`: RTI generally does
not prefix its GUID with its RTPS vendor id (01.01). Known limit, see CHANGELOG.

## Install and license

```
pip install rti.connext
```

Get a Connext Express license from RTI (free, no time limit, system size
capped by the activation key: the documented example limits are 6
DomainParticipants, 12 DataReaders and 12 DataWriters). Request it through the
Connext Express page: <https://www.rti.com/products/connext-express> (RTI also
lists express@rti.com for questions). The activation key arrives as
`rti_license.dat`.

Point Connext at it:

```
# bash
export RTI_LICENSE_FILE=/path/to/rti_license.dat
# PowerShell
$env:RTI_LICENSE_FILE = "C:\path\to\rti_license.dat"
```

`RTI_LICENSE_FILE` takes the full path including the file name. Without it,
Connext also looks for `rti_license.dat` in the working directory and in
`NDDSHOME`
(<https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/installation_guide/license_management.html>).

Run:

```
python scripts/integration/publishers/rti_publisher.py --domain 0
```

## Contract and legal reminders

Source: RTI Free Use Software License Agreement #4046 (rev 03-26),
<https://www.rti.com/hubfs/licenses/Free%20Use%20Software%20License%20Agreement%20%234046%20(rev%2003-26).pdf>,
linked from <https://www.rti.com/get-connext/terms>. Re-read it before relying
on this summary; it is not legal advice.

- No redistribution of the software or of the license (section 3.1, and 2.3.3
  for Express). `rti_license.dat` is yours alone: never commit it.
- Performance, functionality, security or other evaluation results must not be
  disclosed to third parties without RTI's prior written consent
  (confidentiality section; also section 3.5 for performance and vulnerability
  tests).
- No performance benchmarking with this node.
- The agreement has a privacy clause: RTI may collect telemetry about use of
  the software (section 12).

Consequences for this repository:

- RTI runs locally, never in public CI.
- No published capture, screenshot or recording of an RTI run without RTI's
  written agreement.
- Keep `rti_license.dat` and `*.dat` under `scripts/integration/` out of git.
