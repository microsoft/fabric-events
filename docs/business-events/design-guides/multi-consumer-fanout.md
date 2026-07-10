# Multi-Consumer Fanout

Fanout is a pattern where a single Business Event is consumed by multiple independent consumers simultaneously. Because Business Events is a pub/sub system, publishers do not need to know how many consumers exist — each consumer subscribes independently.

## One event, many consumers

```mermaid
flowchart LR
    P[Publisher\nNotebook / UDF / Eventstream] -->|publish| BE[Business Event\n'Retail.Sales.VolumeAlert']
    BE -->|consume| A[Activator\nSend Teams alert]
    BE -->|consume| B[Eventhouse\nStore for analytics]
    BE -->|consume| C[Activator\nTrigger reorder pipeline]
```

Each consumer subscribes independently. Adding a new consumer does not require any change to the publisher.

## Common fanout patterns

### Alert + analytics

The most common combination — one consumer reacts in real time, another stores for historical analysis.

```mermaid
flowchart LR
    P[Publisher] -->|publish| BE[Business Event\n'Retail.Sales.VolumeAlert']
    BE -->|consume| ACT[Activator\nReal-time alert]
    BE -->|consume| EH[Eventhouse\nHistorical analytics]
```

**When to use:** Any event where you need both immediate action and long-term visibility.

### Alert + automation

Two Activator consumers with different actions — one notifies a human, another triggers an automated workflow.

```mermaid
flowchart LR
    P[Publisher] -->|publish| BE[Business Event\n'Retail.Sales.VolumeAlert']
    BE -->|consume| A1[Activator Rule 1\nSend email notification]
    BE -->|consume| A2[Activator Rule 2\nTrigger Power Automate flow]
```

**When to use:** When a signal requires both human awareness and an automated response, and you want to manage them independently.

### Cross-domain fanout

A single business signal consumed by teams in different domains, each with their own logic.

```mermaid
flowchart LR
    P[Publisher\nRetail workload] -->|publish| BE[Business Event\n'Retail.Customer.MilestoneReached']
    BE -->|consume| A[Marketing team\nActivator → loyalty email]
    BE -->|consume| B[Analytics team\nEventhouse → cohort analysis]
    BE -->|consume| C[Finance team\nActivator → discount approval]
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
