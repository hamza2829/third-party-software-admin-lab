# 05 – Queue backlog

## Symptom

- `healthcheck.sh`: `[WARN] queue orders.incoming backlog: 48211 messages (warn above 1000)`.
- Messages are processed late, users see old states; memory or disk alarm on the broker in the worst case.

## Checks

```bash
rabbitmqctl -p lab list_queues name messages messages_ready messages_unacknowledged consumers message_bytes
rabbitmqctl -p lab list_consumers
rabbitmq-diagnostics -q alarms
```

| Observation | Meaning |
|-------------|---------|
| `consumers = 0` | consumer application is down or cannot connect (→ its logs, [04](04-wrong-credentials.md)) |
| `messages_unacknowledged` high, `ready` low | consumer receives but does not ack: hanging or very slow processing, prefetch too high |
| `ready` grows while consumers > 0 | publish rate > processing rate: load peak or consumer too slow |
| `orders.dlq` grows | messages are rejected (poison messages) and dead-lettered |

Message rates: management UI (`http://<host>:15672`, queue page) or `GET /api/queues/lab/orders.incoming`.

## Fix

1. Consumer down → restart it (application owner), check its connection and credentials.
2. Slow consumer → scale out consumers (more instances), lower prefetch so work is shared.
3. Poison messages in the DLQ → inspect a sample (`/api/queues/lab/orders.dlq/get` with `ackmode=ack_requeue_true`),
   give it to the application owner. Re-publish only after the bug is fixed (shovel or a script).
4. Broker memory/disk alarm → [03](03-disk-full.md). Do **not** purge a business queue without written approval of the
   product owner – purged messages are lost.

## Verify

Backlog shrinks over several checks:

```bash
watch -n 10 "rabbitmqctl -q -p lab list_queues name messages consumers"
scripts/healthcheck.sh --product rabbitmq
```

## Escalate

Consumer belongs to a vendor product → support case with queue stats and consumer log excerpt
([vendor-questions.md](../vendor-questions.md)).
