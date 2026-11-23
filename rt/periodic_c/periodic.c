/*
 * Tâche périodique POSIX à réveils absolus (bonus P3).
 *
 * Usage : ./periodic <period_ms> <jobs> <busy_us> <sortie.csv> [cpu]
 *
 * Le thread attend chaque date absolue avec clock_nanosleep(TIMER_ABSTIME),
 * exécute une charge CPU de busy_us, puis enregistre réveil, début et fin.
 * Politique de dépassement : les réveils déjà passés sont sautés, comme
 * dans rt/periodic.py.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define NS_PER_SEC 1000000000LL

typedef struct {
    int64_t release_ns;
    int64_t start_ns;
    int64_t end_ns;
} job_t;

typedef struct {
    int64_t period_ns;
    int64_t busy_ns;
    int jobs;
    int done;
    job_t *records;
} task_t;

static int64_t now_ns(clockid_t clock)
{
    struct timespec ts;
    clock_gettime(clock, &ts);
    return (int64_t)ts.tv_sec * NS_PER_SEC + ts.tv_nsec;
}

static void sleep_until(int64_t target_ns)
{
    struct timespec ts = {
        .tv_sec = target_ns / NS_PER_SEC,
        .tv_nsec = target_ns % NS_PER_SEC,
    };
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR) {
        /* Date absolue : on relance simplement l'attente après un signal. */
    }
}

/* Consomme busy_ns de temps CPU du thread, hors préemptions. */
static void busy_body(int64_t busy_ns)
{
    int64_t end = now_ns(CLOCK_THREAD_CPUTIME_ID) + busy_ns;
    while (now_ns(CLOCK_THREAD_CPUTIME_ID) < end) {
    }
}

static void *periodic_thread(void *arg)
{
    task_t *task = arg;
    int64_t origin = now_ns(CLOCK_MONOTONIC) + 10 * task->period_ns;
    int64_t index = 0;

    for (task->done = 0; task->done < task->jobs; task->done++) {
        int64_t release = origin + index * task->period_ns;
        job_t *job = &task->records[task->done];

        sleep_until(release);
        job->release_ns = release;
        job->start_ns = now_ns(CLOCK_MONOTONIC);
        busy_body(task->busy_ns);
        job->end_ns = now_ns(CLOCK_MONOTONIC);

        index++;
        int64_t late = job->end_ns - (origin + index * task->period_ns);
        if (late > 0)
            index += (late - 1) / task->period_ns + 1;
    }
    return NULL;
}

int main(int argc, char **argv)
{
    if (argc < 5) {
        fprintf(stderr, "Usage : %s <period_ms> <jobs> <busy_us> <sortie.csv> [cpu]\n", argv[0]);
        return 2;
    }

    task_t task = {
        .period_ns = (int64_t)(atof(argv[1]) * 1000000.0),
        .jobs = atoi(argv[2]),
        .busy_ns = (int64_t)(atof(argv[3]) * 1000.0),
    };
    if (task.period_ns <= 0 || task.jobs <= 0 || task.busy_ns < 0) {
        fprintf(stderr, "Paramètres invalides\n");
        return 2;
    }
    task.records = calloc((size_t)task.jobs, sizeof(job_t));
    if (task.records == NULL) {
        perror("calloc");
        return 1;
    }

    pthread_attr_t attr;
    pthread_attr_init(&attr);
    if (argc > 5) {
        cpu_set_t cpus;
        CPU_ZERO(&cpus);
        CPU_SET(atoi(argv[5]), &cpus);
        int rc = pthread_attr_setaffinity_np(&attr, sizeof(cpus), &cpus);
        if (rc != 0)
            fprintf(stderr, "Affinité non appliquée : %s\n", strerror(rc));
    }

    pthread_t thread;
    int rc = pthread_create(&thread, &attr, periodic_thread, &task);
    pthread_attr_destroy(&attr);
    if (rc != 0) {
        fprintf(stderr, "pthread_create : %s\n", strerror(rc));
        free(task.records);
        return 1;
    }
    pthread_join(thread, NULL);

    FILE *out = fopen(argv[4], "w");
    if (out == NULL) {
        perror("fopen");
        free(task.records);
        return 1;
    }
    fprintf(out, "job,release_ns,start_ns,end_ns\n");
    for (int i = 0; i < task.done; i++) {
        fprintf(out, "%d,%" PRId64 ",%" PRId64 ",%" PRId64 "\n", i,
                task.records[i].release_ns, task.records[i].start_ns, task.records[i].end_ns);
    }
    fclose(out);
    free(task.records);
    return 0;
}
