/*
 * C / RTI Connext DDS participant for the TopicForge multi-vendor demo.
 *
 * Joins --domain N (default 0), creates a participant, the topic DemoHeartbeat
 * (type Heartbeat { uint32 seq; }), a RELIABLE DataWriter, and writes seq++ at
 * 1 Hz until it is killed. It reads nothing. A plain functional demo: it
 * measures nothing (RTI license #4046 forbids publishing evaluation results).
 *
 * Usage:
 *   rti_c [--domain N]
 *
 * Needs RTI Connext Professional 7.x and a license file (RTI_LICENSE_FILE).
 * See README.md next to this file and ../RTI.md for the license terms.
 *
 * API sources (RTI Connext C API, rticommunity/rticonnextdds-examples, branch
 * master, commit cacbfb90a2e7772a01218e9663763742d589852a):
 *   - examples/connext_dds/build_systems/cmake/HelloWorld_publisher.c:
 *     DDS_DomainParticipantFactory_create_participant, create_publisher,
 *     <Type>TypeSupport_get_type_name / register_type, create_topic,
 *     create_datawriter, <Type>DataWriter_narrow, <Type>TypeSupport_create_data_ex,
 *     <Type>DataWriter_write, NDDS_Utility_sleep, delete_contained_entities,
 *     delete_participant.
 *   - examples/connext_dds/partitions/c/partitions_publisher.c:
 *     DDS_DataWriterQos_INITIALIZER, DDS_Publisher_get_default_datawriter_qos,
 *     datawriter_qos.reliability.kind = DDS_RELIABLE_RELIABILITY_QOS.
 */

#include "Heartbeat.h"
#include "HeartbeatSupport.h"
#include "ndds/ndds_c.h"

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PROGRAM_NAME "rti_c"
#define TOPIC_NAME "DemoHeartbeat"
#define MAX_DOMAIN_ID 232

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int signum)
{
    (void) signum;
    g_stop = 1;
}

/* Delete all entities. Returns 0 on success. */
static int shutdown_participant(DDS_DomainParticipant *participant)
{
    DDS_ReturnCode_t retcode;
    int status = 0;

    if (participant != NULL) {
        retcode = DDS_DomainParticipant_delete_contained_entities(participant);
        if (retcode != DDS_RETCODE_OK) {
            fprintf(stderr, PROGRAM_NAME ": delete_contained_entities error %d\n", retcode);
            status = -1;
        }
        retcode = DDS_DomainParticipantFactory_delete_participant(
                DDS_TheParticipantFactory, participant);
        if (retcode != DDS_RETCODE_OK) {
            fprintf(stderr, PROGRAM_NAME ": delete_participant error %d\n", retcode);
            status = -1;
        }
    }
    return status;
}

/* Common failure path: message on stderr, clean up, non-zero status. */
static int fail(DDS_DomainParticipant *participant, const char *what)
{
    fprintf(stderr,
            PROGRAM_NAME ": error: %s failed. Entity creation usually fails on a "
            "missing or exhausted RTI Connext license: set RTI_LICENSE_FILE to the "
            "full path of rti_license.dat (see README.md) and check its entity "
            "limits.\n",
            what);
    shutdown_participant(participant);
    return 1;
}

static int parse_domain(int argc, char *argv[], int *domain_id)
{
    int i;
    const char *value = NULL;
    char *end = NULL;
    long parsed;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--domain") == 0 && i + 1 < argc) {
            value = argv[++i];
        } else if (strncmp(argv[i], "--domain=", 9) == 0) {
            value = argv[i] + 9;
        } else {
            fprintf(stderr, "usage: " PROGRAM_NAME " [--domain N]\n");
            return -1;
        }
    }
    if (value == NULL) {
        return 0; /* keep the default */
    }
    parsed = strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 0 || parsed > MAX_DOMAIN_ID) {
        fprintf(stderr, PROGRAM_NAME ": error: --domain must be an integer in 0..%d\n",
                MAX_DOMAIN_ID);
        return -1;
    }
    *domain_id = (int) parsed;
    return 0;
}

static int run(int domain_id)
{
    DDS_DomainParticipant *participant = NULL;
    DDS_Publisher *publisher = NULL;
    DDS_Topic *topic = NULL;
    DDS_DataWriter *writer = NULL;
    HeartbeatDataWriter *heartbeat_writer = NULL;
    Heartbeat *instance = NULL;
    DDS_ReturnCode_t retcode;
    DDS_InstanceHandle_t instance_handle = DDS_HANDLE_NIL;
    struct DDS_DataWriterQos writer_qos = DDS_DataWriterQos_INITIALIZER;
    struct DDS_Duration_t slice = { 0, 100000000 }; /* 100 ms, keeps Ctrl+C responsive */
    const char *type_name = NULL;
    DDS_UnsignedLong seq = 0;
    int tick;

    participant = DDS_DomainParticipantFactory_create_participant(
            DDS_TheParticipantFactory,
            domain_id,
            &DDS_PARTICIPANT_QOS_DEFAULT,
            NULL /* listener */,
            DDS_STATUS_MASK_NONE);
    if (participant == NULL) {
        return fail(participant, "create_participant");
    }

    publisher = DDS_DomainParticipant_create_publisher(
            participant, &DDS_PUBLISHER_QOS_DEFAULT, NULL, DDS_STATUS_MASK_NONE);
    if (publisher == NULL) {
        return fail(participant, "create_publisher");
    }

    type_name = HeartbeatTypeSupport_get_type_name();
    retcode = HeartbeatTypeSupport_register_type(participant, type_name);
    if (retcode != DDS_RETCODE_OK) {
        return fail(participant, "register_type");
    }

    topic = DDS_DomainParticipant_create_topic(
            participant,
            TOPIC_NAME,
            type_name,
            &DDS_TOPIC_QOS_DEFAULT,
            NULL,
            DDS_STATUS_MASK_NONE);
    if (topic == NULL) {
        return fail(participant, "create_topic");
    }

    /* RELIABLE writer; every other policy stays at its default. */
    retcode = DDS_Publisher_get_default_datawriter_qos(publisher, &writer_qos);
    if (retcode != DDS_RETCODE_OK) {
        return fail(participant, "get_default_datawriter_qos");
    }
    writer_qos.reliability.kind = DDS_RELIABLE_RELIABILITY_QOS;

    writer = DDS_Publisher_create_datawriter(
            publisher, topic, &writer_qos, NULL, DDS_STATUS_MASK_NONE);
    DDS_DataWriterQos_finalize(&writer_qos);
    if (writer == NULL) {
        return fail(participant, "create_datawriter");
    }

    heartbeat_writer = HeartbeatDataWriter_narrow(writer);
    if (heartbeat_writer == NULL) {
        return fail(participant, "DataWriter narrow");
    }

    instance = HeartbeatTypeSupport_create_data_ex(DDS_BOOLEAN_TRUE);
    if (instance == NULL) {
        return fail(participant, "create_data");
    }

    printf("[" PROGRAM_NAME "] domain %d: writes " TOPIC_NAME " (RELIABLE)\n", domain_id);
    fflush(stdout);

    while (!g_stop) {
        instance->seq = seq;
        retcode = HeartbeatDataWriter_write(heartbeat_writer, instance, &instance_handle);
        if (retcode != DDS_RETCODE_OK) {
            fprintf(stderr, PROGRAM_NAME ": write error %d\n", retcode);
        }
        ++seq;
        for (tick = 0; tick < 10 && !g_stop; ++tick) {
            NDDS_Utility_sleep(&slice);
        }
    }

    retcode = HeartbeatTypeSupport_delete_data_ex(instance, DDS_BOOLEAN_TRUE);
    if (retcode != DDS_RETCODE_OK) {
        fprintf(stderr, PROGRAM_NAME ": delete_data error %d\n", retcode);
    }
    return shutdown_participant(participant) == 0 ? 0 : 1;
}

int main(int argc, char *argv[])
{
    int domain_id = 0;

    if (parse_domain(argc, argv, &domain_id) != 0) {
        return 2;
    }
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    return run(domain_id);
}
