/*
 * Cyclone DDS (C++) demo participant: writes DemoHeartbeat (RELIABLE, 1 Hz).
 *
 * Contract: scripts/integration/DEMO_CONTRACT.md, section "Language participants".
 *
 * API usage follows the official cyclonedds-cxx tag 11.0.1:
 *   - examples/helloworld/publisher.cpp and CMakeLists.txt (DomainParticipant,
 *     Topic, Publisher, DataWriter, idlcxx_generate, generated all-members
 *     constructor)
 *   - examples/throughput/publisher.cpp (QoS built with operator<< and
 *     dds::core::policy::Reliability::Reliable, signal handling)
 *   - src/ddscxx/include/dds/core/policy/TCorePolicy.hpp (Reliability::Reliable)
 */
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

#include "dds/dds.hpp"
#include "Heartbeat.hpp"

using namespace org::eclipse::cyclonedds;

namespace {

constexpr const char *NAME = "cyclone_cpp";
constexpr const char *TOPIC_NAME = "DemoHeartbeat";
constexpr unsigned long MAX_DOMAIN_ID = 232;

volatile std::sig_atomic_t g_stop = 0;

extern "C" void on_signal(int) { g_stop = 1; }

/* Parse "--domain N" / "--domain=N"; returns false and sets err on bad input. */
bool parse_domain(int argc, char **argv, uint32_t &domain, std::string &err)
{
    domain = 0;
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        std::string value;
        if (arg == "--domain" && i + 1 < argc) {
            value = argv[++i];
        } else if (arg.rfind("--domain=", 0) == 0) {
            value = arg.substr(9);
        } else {
            err = "unknown argument: " + arg;
            return false;
        }
        std::size_t used = 0;
        unsigned long parsed = 0;
        try {
            parsed = std::stoul(value, &used, 10);
        } catch (const std::exception &) {
            used = 0;
        }
        if (value.empty() || used != value.size() || parsed > MAX_DOMAIN_ID) {
            err = "invalid domain id (0-" + std::to_string(MAX_DOMAIN_ID) + "): " + value;
            return false;
        }
        domain = static_cast<uint32_t>(parsed);
    }
    return true;
}

} // namespace

int main(int argc, char **argv)
{
    uint32_t domain_id = 0;
    std::string err;
    if (!parse_domain(argc, argv, domain_id, err)) {
        std::cerr << "[" << NAME << "] error: " << err << "\nusage: " << NAME << " [--domain N]" << std::endl;
        return 2;
    }

    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    try {
        dds::domain::DomainParticipant participant(domain_id);
        dds::topic::Topic<Heartbeat> topic(participant, TOPIC_NAME);
        dds::pub::Publisher publisher(participant);

        dds::pub::qos::DataWriterQos wqos;
        wqos << dds::core::policy::Reliability::Reliable(dds::core::Duration::from_millisecs(100));
        dds::pub::DataWriter<Heartbeat> writer(publisher, topic, wqos);

        std::cout << "[" << NAME << "] domain " << domain_id << ": writes " << TOPIC_NAME
                  << " (RELIABLE)" << std::endl;

        uint32_t seq = 0;
        while (!g_stop) {
            Heartbeat msg(seq++);
            writer.write(msg);
            /* One second in short slices so a signal ends the loop promptly. */
            for (int i = 0; i < 10 && !g_stop; i++) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
    } catch (const dds::core::Exception &e) {
        std::cerr << "[" << NAME << "] error: DDS exception: " << e.what() << std::endl;
        return EXIT_FAILURE;
    } catch (const std::exception &e) {
        std::cerr << "[" << NAME << "] error: " << e.what() << std::endl;
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
