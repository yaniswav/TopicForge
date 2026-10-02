/*
 * Cyclone DDS (C) demo participant: writes DemoHeartbeat (RELIABLE, 1 Hz).
 *
 * Contract: scripts/integration/DEMO_CONTRACT.md, section "Language participants".
 *
 * API usage follows the official Cyclone DDS tag 11.0.1:
 *   - examples/helloworld/publisher.c and CMakeLists.txt (participant, topic,
 *     writer, idlc_generate)
 *   - src/core/ddsc/include/dds/dds.h (dds_create_participant, dds_create_writer,
 *     dds_write, dds_sleepfor, DDS_MSECS)
 *   - src/core/ddsc/include/dds/ddsc/dds_public_qos.h (dds_create_qos,
 *     dds_qset_reliability, dds_delete_qos)
 *   - src/ddsrt/include/dds/ddsrt/retcode.h (dds_strretcode)
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dds/dds.h"
#include "Heartbeat.h"

#define NAME "cyclone_c"
#define TOPIC_NAME "DemoHeartbeat"
#define MAX_DOMAIN_ID 232

static volatile sig_atomic_t g_stop = 0;

static void on_signal (int sig)
{
  (void) sig;
  g_stop = 1;
}

static void fail (const char *what, dds_return_t rc)
{
  fprintf (stderr, "[" NAME "] error: %s: %s\n", what, dds_strretcode (rc < 0 ? -rc : rc));
  exit (EXIT_FAILURE);
}

/* Parse "--domain N" / "--domain=N". Returns the domain id, exits on bad input. */
static dds_domainid_t parse_domain (int argc, char **argv)
{
  unsigned long domain = 0;
  for (int i = 1; i < argc; i++)
  {
    const char *value = NULL;
    if (strcmp (argv[i], "--domain") == 0 && i + 1 < argc)
      value = argv[++i];
    else if (strncmp (argv[i], "--domain=", 9) == 0)
      value = argv[i] + 9;
    else
    {
      fprintf (stderr, "[" NAME "] error: unknown argument: %s\nusage: " NAME " [--domain N]\n", argv[i]);
      exit (2);
    }
    char *end = NULL;
    domain = strtoul (value, &end, 10);
    if (*value == '\0' || *end != '\0' || domain > MAX_DOMAIN_ID)
    {
      fprintf (stderr, "[" NAME "] error: invalid domain id (0-%d): %s\n", MAX_DOMAIN_ID, value);
      exit (2);
    }
  }
  return (dds_domainid_t) domain;
}

int main (int argc, char **argv)
{
  const dds_domainid_t domain = parse_domain (argc, argv);

  signal (SIGINT, on_signal);
  signal (SIGTERM, on_signal);

  dds_entity_t participant = dds_create_participant (domain, NULL, NULL);
  if (participant < 0)
    fail ("dds_create_participant", participant);

  dds_entity_t topic = dds_create_topic (participant, &Heartbeat_desc, TOPIC_NAME, NULL, NULL);
  if (topic < 0)
    fail ("dds_create_topic", topic);

  dds_qos_t *qos = dds_create_qos ();
  dds_qset_reliability (qos, DDS_RELIABILITY_RELIABLE, DDS_MSECS (100));
  dds_entity_t writer = dds_create_writer (participant, topic, qos, NULL);
  dds_delete_qos (qos);
  if (writer < 0)
    fail ("dds_create_writer", writer);

  printf ("[" NAME "] domain %u: writes " TOPIC_NAME " (RELIABLE)\n", (unsigned) domain);
  fflush (stdout);

  Heartbeat msg;
  msg.seq = 0;
  while (!g_stop)
  {
    dds_return_t rc = dds_write (writer, &msg);
    if (rc != DDS_RETCODE_OK)
      fail ("dds_write", rc);
    msg.seq++;
    /* One second in short slices so a signal ends the loop promptly. */
    for (int i = 0; i < 10 && !g_stop; i++)
      dds_sleepfor (DDS_MSECS (100));
  }

  /* Deleting the participant deletes all its children recursively. */
  dds_delete (participant);
  return EXIT_SUCCESS;
}
