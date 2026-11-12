# Multi-Consumer Fanout

Fanout is a pattern where a single Business Event is consumed by multiple independent consumers simultaneously. Because Business Events is a pub/sub system, publishers do not need to know how many consumers exist — each consumer subscribes independently.

## One event, many consumers

```mermaid
flowchart TB
    P["<b>Publisher</b><br/>Notebook / UDF / Eventstream"]
    BE["<b>Business Event</b><br/>'Retail.Sales.VolumeAlert'"]
    A["<b>Activator</b><br/>Send Teams alert"]
    B["<b>Eventhouse</b><br/>Store for analytics"]
    C["<b>Activator</b><br/>Trigger reorder pipeline"]

    P -->|"publish"| BE
    BE -->|"consume"| A
    BE -->|"consume"| B
    BE -->|"consume"| C
```

Each consumer subscribes independently. Adding a new consumer does not require any change to the publisher.

## Common fanout patterns

### Alert + analytics

The most common combination — one consumer reacts in real time, another stores for historical analysis.

```mermaid
flowchart TB
    P["<b>Publisher</b>"]
    BE["<b>Business Event</b><br/>'Retail.Sales.VolumeAlert'"]
    ACT["<b>Activator</b><br/>Real-time alert"]
    EH["<b>Eventhouse</b><br/>Historical analytics"]

    P -->|"publish"| BE
    BE -->|"consume"| ACT
    BE -->|"consume"| EH
```

**When to use:** Any event where you need both immediate action and long-term visibility.

### Alert + automation

Two Activator consumers with different actions — one notifies a human, another triggers an automated workflow.

```mermaid
flowchart TB
    P["<b>Publisher</b>"]
    BE["<b>Business Event</b><br/>'Retail.Sales.VolumeAlert'"]
    A1["<b>Activator Rule 1</b><br/>Send email notification"]
    A2["<b>Activator Rule 2</b><br/>Trigger Power Automate flow"]

    P -->|"publish"| BE
    BE -->|"consume"| A1
    BE -->|"consume"| A2
```

**When to use:** When a signal requires both human awareness and an automated response, and you want to manage them independently.

### Cross-domain fanout

A single business signal consumed by teams in different domains, each with their own logic.

```mermaid
flowchart TB
    P["<b>Publisher</b><br/>Retail workload"]
    BE["<b>Business Event</b><br/>'Retail.Customer.MilestoneReached'"]
    A["<b>Marketing team</b><br/>Activator → loyalty email"]
    B["<b>Analytics team</b><br/>Eventhouse → cohort analysis"]
    C["<b>Finance team</b><br/>Activator → discount approval"]

    P -->|"publish"| BE
    BE -->|"consume"| A
    BE -->|"consume"| B
    BE -->|"consume"| C
```

**When to use:** When a business signal has value across multiple teams or domains, and each team should own their consumer independently.

## How to set up fanout

Each consumer is created independently — there is no fanout configuration on the publisher side.

| Consumer | How to add |
|----------|-----------|
| Activator | Real-Time Hub → Business events → Set alert → configure new rule |
| Eventhouse | Enabled at Business Event creation time via the "Analyze in Eventhouse" checkbox |

To add a second Activator consumer, simply create a new alert rule on the same Business Event. The two rules run independently and do not interfere with each other.

## Design considerations

**Keep consumers independent.** Each consumer should be able to fail, restart, or be updated without affecting others.

**Do not use fanout to sequence work.** If Consumer B must run after Consumer A completes, fanout is not the right pattern — use a dedicated orchestration mechanism instead.

**Monitor each consumer separately.** A delivery failure to one consumer does not affect delivery to others. Track consumer health individually.
