//! Rust / RTI Connector participant for the TopicForge multi-vendor demo.
//!
//! Joins `--domain N` (default 0), creates a participant, the topic
//! `DemoHeartbeat` (type `Heartbeat { uint32 seq; }`) and a RELIABLE writer
//! through an XML application definition (`heartbeat.xml`), then writes
//! `seq++` at 1 Hz until it is killed (Ctrl+C or SIGTERM end the process).
//! A plain functional demo: it measures nothing (RTI license #4046 forbids
//! publishing evaluation results).
//!
//! Domain id: the Connector reads the domain from the XML, so `heartbeat.xml`
//! carries the token `__DOMAIN_ID__`. This program replaces it with the
//! `--domain` value and passes the resulting document as an XML string, using
//! the `str://"<dds>...</dds>"` URL form. That form is documented for the same
//! native library by the Python and JavaScript Connectors
//! (rticommunity/rticonnextdds-connector-py, `Connector(config_name, url)` and
//! rticommunity/rticonnextdds-connector-js); the Rust crate forwards the string
//! unchanged to `RTI_Connector_new` (src/ffi/rtiddsconnector.rs). It was not
//! run against Connector for Rust.
//!
//! API sources (rticommunity/rticonnextdds-connector-rust, tag v1.5.0, commit
//! 0677a6a90d67c998f07e56f9950f792c657264ba):
//!   - docs/guide/getting_started.md and docs/guide/connector.md: `Connector::new`,
//!     `GlobalsDropGuard`, `get_output`.
//!   - examples/shapes/publisher.rs: `take_output`, `output.instance()`,
//!     `set_number`, `output.write()`.
//!   - src/output.rs: `Output::instance`, `Instance::set_number`, `Output::write`.
//!   - docs/guide/configuration.md and examples/shapes/Shapes.xml: XML layout
//!     (`<types>`, `<domain_library>`, `<domain_participant_library>`).

use std::io::Write;
use std::process::ExitCode;
use std::thread;
use std::time::Duration;

use rtiddsconnector::{Connector, GlobalsDropGuard};

const PROGRAM_NAME: &str = "rti_rust";
const TOPIC_NAME: &str = "DemoHeartbeat";
const MAX_DOMAIN_ID: u32 = 232;

const XML_TEMPLATE: &str = include_str!("../heartbeat.xml");
const DOMAIN_TOKEN: &str = "__DOMAIN_ID__";
const PARTICIPANT_NAME: &str = "DemoParticipantLibrary::RtiRustHeartbeat";
const WRITER_NAME: &str = "HeartbeatPublisher::HeartbeatWriter";

/// Parse `--domain N` / `--domain=N`; the default domain is 0.
fn parse_domain(args: &[String]) -> Result<u32, String> {
    let mut value: Option<&str> = None;
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        if arg == "--domain" {
            value = Some(iter.next().ok_or("--domain needs a value")?.as_str());
        } else if let Some(rest) = arg.strip_prefix("--domain=") {
            value = Some(rest);
        } else {
            return Err(format!("unexpected argument '{arg}'"));
        }
    }
    match value {
        None => Ok(0),
        Some(text) => match text.parse::<u32>() {
            Ok(id) if id <= MAX_DOMAIN_ID => Ok(id),
            _ => Err(format!("--domain must be an integer in 0..{MAX_DOMAIN_ID}")),
        },
    }
}

/// The XML application definition for `domain_id`, as a Connext `str://` URL.
fn config_url(domain_id: u32) -> String {
    let xml = XML_TEMPLATE.replace(DOMAIN_TOKEN, &domain_id.to_string());
    format!("str://\"{xml}\"")
}

fn run(domain_id: u32) -> Result<(), String> {
    let _globals = GlobalsDropGuard;

    let connector = Connector::new(PARTICIPANT_NAME, &config_url(domain_id)).map_err(|e| {
        format!(
            "cannot create the RTI Connector participant: {e}\n\
             Entity creation usually fails on a missing or exhausted RTI Connext \
             license: set RTI_LICENSE_FILE to the full path of rti_license.dat \
             (see README.md) and check its entity limits."
        )
    })?;
    let mut output = connector
        .get_output(WRITER_NAME)
        .map_err(|e| format!("cannot get the output {WRITER_NAME}: {e}"))?;

    println!("[{PROGRAM_NAME}] domain {domain_id}: writes {TOPIC_NAME} (RELIABLE)");
    let _ = std::io::stdout().flush();

    let mut seq: u32 = 0;
    loop {
        output
            .instance()
            .set_number("seq", f64::from(seq))
            .map_err(|e| format!("cannot set seq: {e}"))?;
        output.write().map_err(|e| format!("write failed: {e}"))?;
        seq = seq.wrapping_add(1);
        thread::sleep(Duration::from_secs(1));
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let domain_id = match parse_domain(&args) {
        Ok(id) => id,
        Err(message) => {
            eprintln!("{PROGRAM_NAME}: error: {message}");
            eprintln!("usage: {PROGRAM_NAME} [--domain N]");
            return ExitCode::from(2);
        }
    };
    match run(domain_id) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{PROGRAM_NAME}: error: {message}");
            ExitCode::FAILURE
        }
    }
}
