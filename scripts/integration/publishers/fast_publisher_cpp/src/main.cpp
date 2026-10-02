// C++ / Fast DDS 3 participant for the TopicForge multi-vendor demo.
//
// Writes `DemoImu` (BEST_EFFORT, 10 Hz) and reads `DemoOdom` (BEST_EFFORT).
// The Python / Cyclone node writes `DemoOdom` RELIABLE, which a BEST_EFFORT
// reader accepts, so that pair is compatible. The Python / RTI node reads
// `DemoImu` RELIABLE against this BEST_EFFORT writer, an incompatible pair
// that TopicForge's `detect_qos_mismatches` must report.
// See scripts/integration/DEMO_CONTRACT.md.
//
// The two types are built at runtime with the Fast DDS 3 dynamic XTypes API,
// so no fastddsgen and no Java are needed. The type and QoS plumbing follows
// the official example examples/cpp/xtypes (PublisherApp.cpp, SubscriberApp.cpp)
// of eProsima/Fast-DDS, branch master as fetched on 2026-10-01. Participant
// creation with a domain id and a name follows examples/cpp/content_filter
// (tag v3.6.2). Individual signatures were checked against the v3.6.2 headers.
//
// Usage: fast_publisher [--domain N]

#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/dds/publisher/DataWriter.hpp>
#include <fastdds/dds/publisher/Publisher.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/publisher/qos/PublisherQos.hpp>
#include <fastdds/dds/subscriber/DataReader.hpp>
#include <fastdds/dds/subscriber/Subscriber.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <fastdds/dds/subscriber/qos/SubscriberQos.hpp>
#include <fastdds/dds/topic/Topic.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>
#include <fastdds/dds/xtypes/dynamic_types/DynamicData.hpp>
#include <fastdds/dds/xtypes/dynamic_types/DynamicDataFactory.hpp>
#include <fastdds/dds/xtypes/dynamic_types/DynamicPubSubType.hpp>
#include <fastdds/dds/xtypes/dynamic_types/DynamicType.hpp>
#include <fastdds/dds/xtypes/dynamic_types/DynamicTypeBuilder.hpp>
#include <fastdds/dds/xtypes/dynamic_types/DynamicTypeBuilderFactory.hpp>
#include <fastdds/dds/xtypes/dynamic_types/MemberDescriptor.hpp>
#include <fastdds/dds/xtypes/dynamic_types/TypeDescriptor.hpp>

using namespace eprosima::fastdds::dds;

namespace {

volatile std::sig_atomic_t g_stop = 0;

void on_signal(int)
{
    g_stop = 1;
}

using Member = std::pair<std::string, TypeKind>;

// Build a flat struct type named `type_name` from primitive members, in order.
DynamicType::_ref_type make_struct_type(
        const std::string& type_name,
        const std::vector<Member>& members)
{
    TypeDescriptor::_ref_type type_descriptor {traits<TypeDescriptor>::make_shared()};
    type_descriptor->kind(TK_STRUCTURE);
    type_descriptor->name(type_name);
    DynamicTypeBuilder::_ref_type builder = DynamicTypeBuilderFactory::get_instance()->create_type(type_descriptor);
    if (!builder)
    {
        throw std::runtime_error("Error creating type builder for " + type_name);
    }

    for (const Member& member : members)
    {
        MemberDescriptor::_ref_type descriptor {traits<MemberDescriptor>::make_shared()};
        descriptor->name(member.first);
        descriptor->type(DynamicTypeBuilderFactory::get_instance()->get_primitive_type(member.second));
        if (RETCODE_OK != builder->add_member(descriptor))
        {
            throw std::runtime_error("Error adding member " + member.first + " to " + type_name);
        }
    }

    DynamicType::_ref_type type = builder->build();
    if (!type)
    {
        throw std::runtime_error("Error building type " + type_name);
    }
    return type;
}

// Register a dynamic type on the participant and return its type support.
TypeSupport register_type(
        DomainParticipant* participant,
        const DynamicType::_ref_type& type)
{
    TypeSupport support(new DynamicPubSubType(type));
    if (RETCODE_OK != support.register_type(participant))
    {
        throw std::runtime_error("Type registration failed");
    }
    return support;
}

void set_u32(
        const DynamicData::_ref_type& data,
        const std::string& name,
        uint32_t value)
{
    if (RETCODE_OK != data->set_uint32_value(data->get_member_id_by_name(name), value))
    {
        throw std::runtime_error("Error setting " + name);
    }
}

void set_f64(
        const DynamicData::_ref_type& data,
        const std::string& name,
        double value)
{
    if (RETCODE_OK != data->set_float64_value(data->get_member_id_by_name(name), value))
    {
        throw std::runtime_error("Error setting " + name);
    }
}

uint32_t parse_domain(
        int argc,
        char** argv)
{
    uint32_t domain = 0;
    for (int i = 1; i < argc; ++i)
    {
        const std::string arg = argv[i];
        if (arg == "--domain" && i + 1 < argc)
        {
            const long value = std::strtol(argv[++i], nullptr, 10);
            if (value < 0 || value > 232)
            {
                throw std::runtime_error("--domain must be between 0 and 232");
            }
            domain = static_cast<uint32_t>(value);
        }
        else
        {
            throw std::runtime_error("usage: fast_publisher [--domain N]");
        }
    }
    return domain;
}

int run(
        uint32_t domain)
{
    DomainParticipantQos pqos;
    pqos.name("topicforge_demo_fast");
    DomainParticipant* participant = DomainParticipantFactory::get_instance()->create_participant(
        domain, pqos, nullptr, StatusMask::none());
    if (participant == nullptr)
    {
        throw std::runtime_error("Participant initialization failed");
    }

    // Type names and field order are fixed by DEMO_CONTRACT.md.
    DynamicType::_ref_type imu_type = make_struct_type("Imu", {{"seq", TK_UINT32}, {"yaw_rad", TK_FLOAT64}});
    DynamicType::_ref_type odom_type = make_struct_type(
        "Odom", {{"seq", TK_UINT32}, {"x", TK_FLOAT64}, {"y", TK_FLOAT64}});
    TypeSupport imu_support = register_type(participant, imu_type);
    TypeSupport odom_support = register_type(participant, odom_type);

    Publisher* publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    Subscriber* subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    Topic* imu_topic = participant->create_topic("DemoImu", imu_support.get_type_name(), TOPIC_QOS_DEFAULT);
    Topic* odom_topic = participant->create_topic("DemoOdom", odom_support.get_type_name(), TOPIC_QOS_DEFAULT);
    if (publisher == nullptr || subscriber == nullptr || imu_topic == nullptr || odom_topic == nullptr)
    {
        throw std::runtime_error("Publisher, subscriber or topic initialization failed");
    }

    // The contract asks for BEST_EFFORT and VOLATILE. Fast DDS defaults a writer
    // to RELIABLE and TRANSIENT_LOCAL, so both are set explicitly. History is
    // already KEEP_LAST 1 by default.
    DataWriterQos writer_qos = DATAWRITER_QOS_DEFAULT;
    publisher->get_default_datawriter_qos(writer_qos);
    writer_qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
    writer_qos.durability().kind = VOLATILE_DURABILITY_QOS;
    DataWriter* writer = publisher->create_datawriter(imu_topic, writer_qos);

    DataReaderQos reader_qos = DATAREADER_QOS_DEFAULT;
    subscriber->get_default_datareader_qos(reader_qos);
    reader_qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
    DataReader* reader = subscriber->create_datareader(odom_topic, reader_qos);

    if (writer == nullptr || reader == nullptr)
    {
        throw std::runtime_error("DataWriter or DataReader initialization failed");
    }

    std::cout << "[fast_publisher] domain " << domain
              << ": writes DemoImu (BEST_EFFORT), reads DemoOdom (BEST_EFFORT)" << std::endl;

    DynamicData::_ref_type sample = DynamicDataFactory::get_instance()->create_data(imu_type);
    if (!sample)
    {
        throw std::runtime_error("Error creating dynamic data");
    }

    const double two_pi = 6.283185307179586;
    uint32_t seq = 0;
    while (g_stop == 0)
    {
        set_u32(sample, "seq", seq);
        set_f64(sample, "yaw_rad", std::fmod(seq * 0.05, two_pi));
        writer->write(&sample);
        ++seq;
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    participant->delete_contained_entities();
    DomainParticipantFactory::get_instance()->delete_participant(participant);
    std::cout << "[fast_publisher] stopped after " << seq << " samples" << std::endl;
    return 0;
}

} // namespace

int main(
        int argc,
        char** argv)
{
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    try
    {
        return run(parse_domain(argc, argv));
    }
    catch (const std::exception& e)
    {
        std::cerr << "error: " << e.what() << std::endl;
        return 1;
    }
}
