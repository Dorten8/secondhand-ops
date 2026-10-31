# Delivery routing — where the pipeline's output goes

This document exists because the destination of a message is **deployment
configuration, not code**. Any user of this pipeline must be able to point it at their
own chat without touching a script. Nothing in this repository may hardcode a chat id,
a topic, a channel, or a phone number.

## 1. The rule

The pipeline emits **events**. A *sink* consumes them. The sink is chosen by config.

| Event | Emitted by | Typical sink |
|---|---|---|
| `buyer_message` | `poll` | the operator's working chat |
| `draft_ready` | `draft` | the operator's working chat, with approve/reply options |
| `delivery_verified` | `send` | the same chat, as a receipt |
| `delivery_failed` | `send` | same chat, marked as failure — never auto-retried |
| `item_sold` | registry watch | the same chat, so open drafts get closed |

A sink is anything that can accept a short text message with a stable id: a Telegram
chat (optionally a forum topic), a Discord channel, an HTTP webhook, an email, or
`stdout` for a dry run.

## 2. Configuration

One place, environment or config file — never inline in a module:

```bash
# --- sink selection -------------------------------------------------
DELIVERY_SINK=telegram            # telegram | discord | webhook | stdout

# --- telegram -------------------------------------------------------
DELIVERY_TELEGRAM_CHAT_ID=<chat id>
DELIVERY_TELEGRAM_TOPIC_ID=<topic/thread id, optional>

# --- discord --------------------------------------------------------
DELIVERY_DISCORD_CHANNEL_ID=<channel id>

# --- webhook --------------------------------------------------------
DELIVERY_WEBHOOK_URL=<https endpoint>
```

Swapping sinks is a config change and a restart. No code edits, no branch.

## 3. What a sink must guarantee

1. **One event, one message.** The event id is the idempotency key; a repeated tick
   must not re-deliver.
2. **Never invent a destination.** If the config is missing or unparseable, the sink
   **fails loudly at startup** and writes to the local log — it does not fall back to
   "some default chat", because that sends production traffic to the wrong place.
3. **Idle is silent.** No heartbeat, no "still running", no presence messages. A sink
   that cannot stay quiet is not usable by this pipeline.
4. **A failure is reported as a failure.** Especially for `delivery_failed` — never
   silently swallowed.

## 4. This deployment

For reference only — **not** a default for anyone else:

```
PRODUCTION CHAT  = the operator's Telegram "Selling bikes here" topic
                   (all buyer-facing pipeline output lands there)
DEVELOPMENT      = Discord #agent-bridge and the repo's own docs
```

The point of the split: buyer messages and draft approvals go where the operator sells;
design discussion and agent coordination stay where the work happens. Mixing them
buries a real buyer behind engineering chatter.

If someone clones this repo, section 4 does not apply to them. They set section 2.
