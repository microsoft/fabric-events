# Decision Guide

Microsoft Fabric Real-Time Intelligence offers multiple ways to connect workloads and move data between services. Use this guide to choose the right approach for your scenario.

## Capabilities at a glance

| Capability | What it is | Best for |
|---|---|---|
| **[Business Events](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-overview)** | Schema-defined signals published by Fabric workloads to Real-Time Hub | Discrete, meaningful occurrences that need to reach one or more consumers independently |
| **[Eventstream](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview)** | High-throughput streaming pipeline for continuous data | IoT telemetry, clickstreams, logs, and any high-volume continuous data |
| **[Activator](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/activator/activator-introduction)** | Rules engine that monitors data and triggers actions | Reacting to conditions in streams or Business Events without writing code |
| **Direct API call** | HTTP call made from a Notebook or User Data Function to an external service | When you need a synchronous response from a service outside Fabric |

## Should I use Business Events?

```mermaid
flowchart TD
    A[I need to signal that\nsomething happened] --> B{Is it a continuous\nstream of data?}
    B -->|Yes: high-volume, ongoing| Z1[Use Eventstream]
    B -->|No: a discrete condition occurred| C{Do other workloads\nneed to act on this?}
    C -->|Yes| E[Use Business Events ✅]
    C -->|No: I need an immediate\nresponse to continue| Z2[Make a direct API call\nfrom a Notebook or UDF]
```

## When to use each option in Fabric

**Use Business Events when:**

- A Notebook finishes a transformation and Activator or Eventhouse need to react
- A threshold condition is detected and multiple consumers need to be notified independently
- You want to add a new consumer without modifying the publisher
- You need a schema contract that enforces what data the event carries

**Use Eventstream when:**

- You are ingesting continuous data from IoT sensors, event producers, or external systems
- You need to process or route high-volume data streams in real time
- The data is a stream, not a discrete signal

**Use Activator directly when:**

- You want to monitor an existing Eventstream or Eventhouse table for conditions
- You need to trigger alerts or actions without publishing an explicit event
- The logic lives entirely within Activator and no other consumer needs the signal

**Make a direct API call when:**

- You are in a Notebook or User Data Function and need a synchronous response from an external service
- The call is to a system outside Fabric (a REST API, a database, an external platform)
- You need the result immediately to continue execution in the same workload
