// C++ (Modern C++ API) / RTI Connext DDS participant for the TopicForge
// multi-vendor demo.
//
// Joins --domain N (default 0), creates a participant, the topic DemoHeartbeat
// (type Heartbeat { uint32 seq; }), a RELIABLE DataWriter, and writes seq++ at
// 1 Hz until it is killed. It reads nothing. A plain functional demo: it
// measures nothing (RTI license #4046 forbids publishing evaluation results).
//
// Usage:
//   rti_cpp [--domain N]
//
// Needs RTI Connext Professional 7.x and a license file (RTI_LICENSE_FILE).
// See README.md next to this file and ../RTI.md for the license terms.
//
// API sources (RTI Connext Modern C++ API, rticommunity/rticonnextdds-examples,
// branch master, commit cacbfb90a2e7772a01218e9663763742d589852a):
//   - examples/connext_dds/asynchronous_publication/c++11/async_publisher.cxx:
//     dds::domain::DomainParticipant(domain_id), dds::topic::Topic<T>,
//     dds::core::QosProvider::Default().datawriter_qos(), dds::pub::Publisher,
//     dds::pub::DataWriter<T>(publisher, topic, qos), writer.write(sample),
//     rti::util::sleep, generated type with public members (instance.x = ...).
//   - examples/connext_dds/group_coherent_presentation/c++11/
//     GroupCoherentExample_publisher.cxx:
//     writer_qos << Reliability::Reliable() (dds::core::policy).
//   - examples/connext_dds/asynchronous_publication/c++11/application.hpp:
//     csignal-based SIGINT / SIGTERM handling.

#include <csignal>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>

#include <dds/pub/ddspub.hpp>
#include <rti/util/util.hpp>

#include "Heartbeat.hpp"

namespace {

const char *const kProgramName = "rti_cpp";
const char *const kTopicName = "DemoHeartbeat";
const long kMaxDomainId = 232;

volatile std::sig_atomic_t g_stop = 0;

void on_signal(int)
{
    g_stop = 1;
}

// Returns false (after printing a message) on a malformed command line.
bool parse_domain(int argc, char *argv[], unsigned int &domain_id)
{
    const std::string flag = "--domain";
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        std::string value;
        if (arg == flag && i + 1 < argc) {
            value = argv[++i];
        } else if (arg.compare(0, flag.size() + 1, flag + "=") == 0) {
            value = arg.substr(flag.size() + 1);
        } else {
            std::cerr << "usage: " << kProgramName << " [--domain N]" << std::endl;
            return false;
        }
        char *end = nullptr;
        const long parsed = std::strtol(value.c_str(), &end, 10);
        if (value.empty() || *end != '\0' || parsed < 0 || parsed > kMaxDomainId) {
            std::cerr << kProgramName << ": error: --domain must be an integer in 0.."
                      << kMaxDomainId << std::endl;
            return false;
        }
        domain_id = static_cast<unsigned int>(parsed);
    }
    return true;
}

void run_publisher_application(unsigned int domain_id)
{
    dds::domain::DomainParticipant participant(domain_id);

    dds::topic::Topic<Heartbeat> topic(participant, kTopicName);

    // RELIABLE writer; every other policy stays at its default.
    dds::pub::qos::DataWriterQos writer_qos =
            dds::core::QosProvider::Default().datawriter_qos();
    writer_qos << dds::core::policy::Reliability::Reliable();

    dds::pub::Publisher publisher(participant);
    dds::pub::DataWriter<Heartbeat> writer(publisher, topic, writer_qos);

    std::cout << "[" << kProgramName << "] domain " << domain_id << ": writes "
              << kTopicName << " (RELIABLE)" << std::endl;

    Heartbeat sample;
    unsigned int seq = 0;
    while (!g_stop) {
        sample.seq = seq++;
        writer.write(sample);
        // 10 x 100 ms keeps Ctrl+C responsive.
        for (int tick = 0; tick < 10 && !g_stop; ++tick) {
            rti::util::sleep(dds::core::Duration::from_millisecs(100));
        }
    }
}

}  // namespace

int main(int argc, char *argv[])
{
    unsigned int domain_id = 0;
    if (!parse_domain(argc, argv, domain_id)) {
        return 2;
    }
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    try {
        run_publisher_application(domain_id);
    } catch (const std::exception &ex) {
        // DDS errors surface as std::exception subclasses.
        std::cerr << kProgramName << ": error: " << ex.what()
                  << "\nEntity creation usually fails on a missing or exhausted "
                     "RTI Connext license: set RTI_LICENSE_FILE to the full path "
                     "of rti_license.dat (see README.md) and check its entity "
                     "limits."
                  << std::endl;
        return 1;
    }

    // Releases the memory used by the participant factory. Optional at exit.
    dds::domain::DomainParticipant::finalize_participant_factory();
    return 0;
}
