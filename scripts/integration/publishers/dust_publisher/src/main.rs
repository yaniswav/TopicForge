//! Rust / Dust DDS participant for the TopicForge multi-vendor demo.
//!
//! Publishes a fake lidar range on `DemoLidarScan` until stopped. The writer
//! is BEST_EFFORT on purpose: the Python / Cyclone node subscribes to the same
//! topic with a RELIABLE reader, an incompatible pair that TopicForge's
//! `detect_qos_mismatches` must report.
//!
//! Usage: dust_publisher [--domain N] [--reliable]

use dust_dds::{
    domain::domain_participant_factory::DomainParticipantFactory,
    infrastructure::{
        listener::NO_LISTENER,
        qos::{DataWriterQos, QosKind},
        qos_policy::{ReliabilityQosPolicy, ReliabilityQosPolicyKind},
        status::NO_STATUS,
        time::{Duration, DurationKind},
        type_support::DdsType,
    },
};

#[derive(DdsType, Debug)]
struct LidarScan {
    seq: u32,
    range_m: f32,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let domain_id: i32 = args
        .iter()
        .position(|a| a == "--domain")
        .and_then(|i| args.get(i + 1))
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let reliable = args.iter().any(|a| a == "--reliable");

    let participant = DomainParticipantFactory::get_instance()
        .create_participant(domain_id, QosKind::Default, NO_LISTENER, NO_STATUS)
        .expect("create participant");
    let topic = participant
        .create_topic::<LidarScan>(
            "DemoLidarScan",
            "LidarScan",
            QosKind::Default,
            NO_LISTENER,
            NO_STATUS,
        )
        .expect("create topic");
    let publisher = participant
        .create_publisher(QosKind::Default, NO_LISTENER, NO_STATUS)
        .expect("create publisher");

    let kind = if reliable {
        ReliabilityQosPolicyKind::Reliable
    } else {
        ReliabilityQosPolicyKind::BestEffort
    };
    let writer_qos = DataWriterQos {
        reliability: ReliabilityQosPolicy {
            kind,
            max_blocking_time: DurationKind::Finite(Duration::new(0, 100_000_000)),
        },
        ..Default::default()
    };
    let writer = publisher
        .create_datawriter(&topic, QosKind::Specific(writer_qos), NO_LISTENER, NO_STATUS)
        .expect("create writer");

    println!(
        "[dust_publisher] domain {domain_id}, topic DemoLidarScan, reliability {}",
        if reliable { "RELIABLE" } else { "BEST_EFFORT" }
    );
    let mut seq: u32 = 0;
    loop {
        let range_m = 2.0 + (seq % 50) as f32 * 0.1;
        writer.write(LidarScan { seq, range_m }, None).expect("write");
        seq = seq.wrapping_add(1);
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}
