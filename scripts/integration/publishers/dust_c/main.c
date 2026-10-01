/*
 * C / Dust DDS participant for the TopicForge multi-vendor demo.
 *
 * Writes DemoHeartbeat (struct Heartbeat { uint32 seq; }, RELIABLE) at 1 Hz
 * and nothing else, so that list_participants shows one Dust DDS participant
 * written in C. Runs until killed.
 *
 * Usage: dust_c_publisher [--domain N]
 *
 * Heartbeat.h is generated from Heartbeat.idl by dust_dds_gen (see build.sh).
 * API calls follow bindings/c/tests/test_hello_world.c in s2e-systems/dust-dds.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "Heartbeat.h"
#include "dust_dds.h"

#ifdef _WIN32
#include <windows.h>
static void sleep_one_second(void) { Sleep(1000); }
#else
#include <unistd.h>
static void sleep_one_second(void) { sleep(1); }
#endif

static int parse_domain(int argc, char **argv)
{
    for (int i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], "--domain") == 0) {
            return atoi(argv[i + 1]);
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    int domain = parse_domain(argc, argv);

    DDS_DomainParticipantFactory *factory =
        (DDS_DomainParticipantFactory *)DDS_DomainParticipantFactory_get_instance();
    if (factory == NULL) {
        fprintf(stderr, "error: Dust DDS factory unavailable\n");
        return 1;
    }

    DDS_DomainParticipant *participant = DDS_DomainParticipantFactory_create_participant(
        factory, domain, PARTICIPANT_QOS_DEFAULT, NULL, 0);
    if (participant == NULL) {
        fprintf(stderr, "error: create_participant failed on domain %d\n", domain);
        return 1;
    }

    DDS_Topic *topic = DDS_DomainParticipant_create_topic(
        participant, "DemoHeartbeat", "Heartbeat", TOPIC_QOS_DEFAULT, NULL, 0,
        (DDS_DynamicType *)Heartbeat_get_type());
    if (topic == NULL) {
        fprintf(stderr, "error: create_topic DemoHeartbeat failed\n");
        return 1;
    }

    DDS_Publisher *publisher = DDS_DomainParticipant_create_publisher(
        participant, PUBLISHER_QOS_DEFAULT, NULL, 0);
    if (publisher == NULL) {
        fprintf(stderr, "error: create_publisher failed\n");
        return 1;
    }

    DDS_DataWriterQos writer_qos = DDS_DataWriter_qos_default();
    writer_qos.reliability.kind = RELIABLE_RELIABILITY_QOS;
    writer_qos.reliability.max_blocking_time.sec = 1;
    writer_qos.reliability.max_blocking_time.nanosec = 0;

    DDS_DataWriter *writer = DDS_Publisher_create_datawriter(
        publisher, topic, &writer_qos, NULL, 0);
    if (writer == NULL) {
        fprintf(stderr, "error: create_datawriter failed\n");
        return 1;
    }

    printf("[dust_c] domain %d: writes DemoHeartbeat (RELIABLE)\n", domain);
    fflush(stdout);

    struct Heartbeat sample;
    sample.seq = 0;
    for (;;) {
        DDS_ReturnCode rc = HeartbeatDataWriter_write(writer, &sample, NULL);
        if (rc != DDS_RETCODE_OK) {
            fprintf(stderr, "error: write failed with return code %d\n", (int)rc);
            return 1;
        }
        sample.seq++;
        sleep_one_second();
    }
}
