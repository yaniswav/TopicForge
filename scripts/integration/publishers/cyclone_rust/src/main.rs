//! Cyclone DDS (Rust) demo participant: writes `DemoHeartbeat` (RELIABLE, 1 Hz).
//!
//! Contract: scripts/integration/DEMO_CONTRACT.md, section "Language participants".
//!
//! API usage follows the official crate at tag 0.0.4
//! (https://github.com/eclipse-cyclonedds/cyclonedds-rust):
//!   - README.md "Example" (derive `Topicable`, `Domain`, `Participant`, `Topic`,
//!     `QoS::with_reliability`, `Writer::builder(..).with_qos(..).build()`)
//!   - cyclonedds/examples/pub.rs (writer lifecycle)
//!   - cyclonedds-macros/src/lib.rs (`#[dds(type_name = "...")]` override)

use std::io::Write;
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration as StdDuration;

use cyclonedds::qos::policy;
use cyclonedds::{Domain, Duration, Participant, QoS, Topic, Topicable, Writer};

const NAME: &str = "cyclone_rust";
const TOPIC: &str = "DemoHeartbeat";
const MAX_DOMAIN_ID: u32 = 232;

/// `struct Heartbeat { uint32 seq; };` with the bare IDL type name `Heartbeat`.
#[derive(Topicable, serde::Serialize, serde::Deserialize, Clone, Default, Debug)]
#[dds(type_name = "Heartbeat")]
struct Heartbeat {
    seq: u32,
}

/// Parse `--domain N` (default 0) from the command line.
fn parse_domain() -> Result<u32, String> {
    let mut domain = 0u32;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let value = if arg == "--domain" {
            args.next().ok_or("--domain needs a value")?
        } else if let Some(v) = arg.strip_prefix("--domain=") {
            v.to_string()
        } else {
            return Err(format!("unknown argument: {arg}"));
        };
        domain = value
            .parse::<u32>()
            .map_err(|_| format!("invalid domain id: {value}"))?;
        if domain > MAX_DOMAIN_ID {
            return Err(format!("domain id out of range 0-{MAX_DOMAIN_ID}: {domain}"));
        }
    }
    Ok(domain)
}

fn run(domain_id: u32, stop: &AtomicBool) -> cyclonedds::Result<()> {
    let domain = Domain::new(domain_id)?;
    let participant = Participant::new(&domain)?;
    let topic = Topic::<Heartbeat>::new(&participant, TOPIC)?;
    let qos = QoS::new().with_reliability(policy::Reliability::Reliable {
        max_blocking_time: Duration::from_millis(100),
    });
    let writer = Writer::builder(&topic).with_qos(&qos).build()?;

    println!("[{NAME}] domain {domain_id}: writes {TOPIC} (RELIABLE)");
    std::io::stdout().flush().ok();

    let mut seq: u32 = 0;
    'outer: while !stop.load(Ordering::SeqCst) {
        writer.write(&Heartbeat { seq })?;
        seq = seq.wrapping_add(1);
        // Sleep one second in short slices so a signal ends the loop promptly.
        for _ in 0..10 {
            if stop.load(Ordering::SeqCst) {
                break 'outer;
            }
            std::thread::sleep(StdDuration::from_millis(100));
        }
    }
    Ok(())
}

fn main() -> ExitCode {
    let domain_id = match parse_domain() {
        Ok(d) => d,
        Err(msg) => {
            eprintln!("[{NAME}] error: {msg}\nusage: {NAME} [--domain N]");
            return ExitCode::from(2);
        }
    };

    let stop = Arc::new(AtomicBool::new(false));
    let handler_stop = Arc::clone(&stop);
    if let Err(err) = ctrlc::set_handler(move || handler_stop.store(true, Ordering::SeqCst)) {
        eprintln!("[{NAME}] error: cannot install signal handler: {err}");
        return ExitCode::FAILURE;
    }

    match run(domain_id, &stop) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("[{NAME}] error: DDS failure: {err}");
            ExitCode::FAILURE
        }
    }
}
