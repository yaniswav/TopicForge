/*
 * C / OpenSplice (SAC API) participant for the TopicForge multi-vendor demo.
 *
 * EXPERIMENTAL. Publishes `Status { seq; text; }` on the topic DemoStatus at
 * 10 Hz, RELIABLE, until killed. Nobody reads it: TopicForge must show the
 * publication, nothing else (see scripts/integration/DEMO_CONTRACT.md).
 *
 * The API calls are modelled on the official OpenSplice 6.9 example
 *   examples/dcps/HelloWorld/c/src/HelloWorldDataPublisher.c
 *   examples/dcps/HelloWorld/c/src/DDSEntitiesManager.c
 *   examples/dcps/HelloWorld/c/src/CheckStatus.c
 * of https://github.com/ADLINK-IST/opensplice (master, release V6_9_210323OSS).
 * Every call below marked "(example)" appears verbatim in that example. Calls
 * marked "(inferred)" follow the idlpp naming pattern of the example, applied
 * to a type without module: HelloWorldData_Msg -> Status.
 *
 * Domain: OpenSplice takes the domain from the configuration pointed to by
 * OSPL_URI (element Domain/Id), not from this program. The participant is
 * created with DDS_DOMAIN_ID_DEFAULT; --domain N is only echoed. run_ospl.sh
 * and run_ospl.bat patch Domain/Id in a copy of the XML when N is not 0.
 *
 * Usage: ospl_publisher [--domain N]
 */

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <unistd.h>
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dds_dcps.h"
#include "Status.h"
#include "StatusSacDcps.h"

#define PERIOD_MS 100
#define TEXT_CAPACITY 63

/* Same behaviour as checkStatus() in the example's CheckStatus.c. */
static void check_status(DDS_ReturnCode_t status, const char *info)
{
    if (status != DDS_RETCODE_OK && status != DDS_RETCODE_NO_DATA) {
        fprintf(stderr, "[ospl_publisher] error in %s: return code %d\n",
                info, (int)status);
        exit(1);
    }
}

/* Same behaviour as checkHandle() in the example's CheckStatus.c. */
static void check_handle(void *handle, const char *info)
{
    if (!handle) {
        fprintf(stderr, "[ospl_publisher] error in %s: creation failed\n", info);
        exit(1);
    }
}

static void sleep_ms(unsigned int ms)
{
#ifdef _WIN32
    Sleep(ms);
#else
    usleep(ms * 1000u);
#endif
}

int main(int argc, char *argv[])
{
    DDS_DomainParticipantFactory factory;
    DDS_DomainParticipant participant;
    DDS_TypeSupport type_support;
    DDS_TopicQos *topic_qos;
    DDS_DataWriterQos *writer_qos;
    DDS_Topic topic;
    DDS_Publisher publisher;
    DDS_DataWriter writer;
    DDS_ReturnCode_t status;
    char *type_name;
    Status *sample;
    unsigned long seq = 0;
    int requested_domain = 0;
    int i;

    for (i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], "--domain") == 0) {
            requested_domain = atoi(argv[i + 1]);
        }
    }

    /* Participant (example: createParticipant in DDSEntitiesManager.c). */
    factory = DDS_DomainParticipantFactory_get_instance();
    check_handle(factory, "DDS_DomainParticipantFactory_get_instance");
    participant = DDS_DomainParticipantFactory_create_participant(
        factory, DDS_DOMAIN_ID_DEFAULT, DDS_PARTICIPANT_QOS_DEFAULT, NULL,
        DDS_STATUS_MASK_NONE);
    check_handle(participant, "DDS_DomainParticipantFactory_create_participant");

    /* Type registration (example: HelloWorldDataPublisher.c, registerMessageType). */
    type_support = StatusTypeSupport__alloc();               /* (inferred) */
    check_handle(type_support, "StatusTypeSupport__alloc");
    type_name = StatusTypeSupport_get_type_name(type_support); /* (inferred) */
    check_handle(type_name, "StatusTypeSupport_get_type_name");
    status = StatusTypeSupport_register_type(                /* (inferred) */
        type_support, participant, type_name);
    check_status(status, "StatusTypeSupport_register_type");

    /* Topic, RELIABLE (example: createTopic). Durability stays VOLATILE and
     * history KEEP_LAST 1, the defaults the contract asks for. */
    topic_qos = DDS_TopicQos__alloc();
    check_handle(topic_qos, "DDS_TopicQos__alloc");
    status = DDS_DomainParticipant_get_default_topic_qos(participant, topic_qos);
    check_status(status, "DDS_DomainParticipant_get_default_topic_qos");
    topic_qos->reliability.kind = DDS_RELIABLE_RELIABILITY_QOS;
    topic = DDS_DomainParticipant_create_topic(
        participant, "DemoStatus", type_name, topic_qos, NULL,
        DDS_STATUS_MASK_NONE);
    check_handle(topic, "DDS_DomainParticipant_create_topic");
    DDS_free(topic_qos);
    DDS_free(type_name);
    DDS_free(type_support);

    /* Publisher with default QoS, hence the empty partition (example:
     * createPublisher, minus its custom partition). */
    publisher = DDS_DomainParticipant_create_publisher(
        participant, DDS_PUBLISHER_QOS_DEFAULT, NULL, DDS_STATUS_MASK_NONE);
    check_handle(publisher, "DDS_DomainParticipant_create_publisher");

    /* DataWriter, explicitly RELIABLE (example: createDataWriter). */
    writer_qos = DDS_DataWriterQos__alloc();
    check_handle(writer_qos, "DDS_DataWriterQos__alloc");
    status = DDS_Publisher_get_default_datawriter_qos(publisher, writer_qos);
    check_status(status, "DDS_Publisher_get_default_datawriter_qos");
    writer_qos->reliability.kind = DDS_RELIABLE_RELIABILITY_QOS;
    writer_qos->writer_data_lifecycle.autodispose_unregistered_instances = FALSE;
    writer = DDS_Publisher_create_datawriter(
        publisher, topic, writer_qos, NULL, DDS_STATUS_MASK_NONE);
    check_handle(writer, "DDS_Publisher_create_datawriter");
    DDS_free(writer_qos);

    /* Sample, reused for every write (example: HelloWorldData_Msg__alloc,
     * DDS_string_alloc). */
    sample = Status__alloc();                                /* (inferred) */
    check_handle(sample, "Status__alloc");
    sample->text = DDS_string_alloc(TEXT_CAPACITY);
    check_handle(sample->text, "DDS_string_alloc");

    printf("[ospl_publisher] domain %d (taken from OSPL_URI), topic DemoStatus, "
           "type Status, reliability RELIABLE\n", requested_domain);
    fflush(stdout);

    for (;;) {
        sample->seq = seq;
        snprintf(sample->text, TEXT_CAPACITY + 1, "status %lu", seq);
        status = StatusDataWriter_write(writer, sample, DDS_HANDLE_NIL); /* (inferred) */
        check_status(status, "StatusDataWriter_write");
        seq++;
        sleep_ms(PERIOD_MS);
    }

    /* Not reached: the program runs until killed. */
    return 0;
}
