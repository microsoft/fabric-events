# Customer Loyalty Milestone

**Publisher:** Activator | **Consumer:** Activator

## Business context

A retail company already tracks cumulative customer spending across stores. When a customer reaches a loyalty milestone, such as $5,000 in spend, marketing needs to issue a reward, analytics needs to record the occurrence, and the CRM needs the new loyalty tier.

Each team owns a different system and response. They need a consistent definition of the milestone without having to reproduce the spending threshold in every application or coordinate every downstream update through the detection rule.

**The problem without Business Events:**
A shared rule or existing integrations can deliver the first implementation. If detection and all responses are maintained together, adding a team or changing a response requires coordination with the owner of that implementation. Separate detection rules can instead drift in their interpretation of the milestone.

**The solution with Business Events:**
Activator keeps detecting the milestone and publishes a `Retail.Customer.MilestoneReached` event. Marketing and CRM responses use independent Activator rules, and Eventhouse records received events for analytics. Teams can add responses using the existing contract without changing detection; changes to the milestone definition still require agreement.

## Architecture

```mermaid
flowchart TB
    subgraph Eventhouse
        KQL[KQL Query\nCumulative spend\nper customer]
    end

    subgraph Activator Publisher
        RULE1{Condition:\ntotal_spend > threshold}
    end

    subgraph Real-Time Hub
        BE([Business Event\n'Retail.Customer.MilestoneReached'])
    end

    subgraph Consumers
        PA[Activator → Power Automate\nReward notification]
        EH[(Eventhouse\nMilestone history)]
        CRM[Activator → Power Automate\nCRM tier update]
    end

    KQL -->|Monitor| RULE1
    RULE1 -->|Publish event| BE
    BE --> PA
    BE --> EH
    BE --> CRM
```

## Step 1: Create the Business Event

Before configuring Activator, define the Business Event in Real-Time Hub.

1. Go to [Real-Time Hub → Business Events → Create](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/create-business-events).
2. Create or select an Event Schema Set. Use `RetailCustomers` as the schema set name.
3. Name the event `Retail.Customer.MilestoneReached`.
4. In the schema editor, paste the following JSON:

    ```json
    {
      "type": "record",
      "name": "Retail.Customer.MilestoneReached",
      "fields": [
        {
          "name": "customer_id",
          "type": "string",
          "doc": "Unique identifier of the customer"
        },
        {
          "name": "customer_name",
          "type": "string",
          "doc": "Full name of the customer"
        },
        {
          "name": "milestone_tier",
          "type": "string",
          "doc": "Loyalty tier reached: silver, gold, or platinum"
        },
        {
          "name": "total_spend",
          "type": "float",
          "doc": "Cumulative spend amount that triggered this milestone"
        },
        {
          "name": "store_id",
          "type": "string",
          "doc": "Identifier of the store where the milestone was reached"
        },
        {
          "name": "detected_at",
          "type": "string",
          "doc": "Timestamp when the milestone was detected, ISO 8601 format"
        }
      ]
    }
    ```

5. Select **Create**.

## Step 2: Publisher - Activator

Activator monitors a KQL query that computes cumulative customer spend. When the condition is met, it publishes the Business Event. There is no code to write.

### Create the Activator rule

1. In your Fabric workspace, open your KQL Queryset that queries the customer transaction table in your Eventhouse.
2. Write or select a query that returns cumulative spend per customer, for example:

    ```kusto
    transactions
    | summarize total_spend = sum(amount) by customer_id, customer_name, store_id
    | where total_spend > 5000
    ```

3. Select **Set alert** from the KQL Queryset toolbar.
4. In the Activator rule pane, configure the condition:
    - **Monitor**: select the query result field `total_spend`
    - **Condition**: `Becomes greater than`
    - **Value**: `5000`

### Configure the publish action

5. In the **Action** section, select **Publish a business event**.
6. For **Business event**, select `Retail.Customer.MilestoneReached`.
7. Map each schema field to the corresponding query result column or a static value:

    | Schema field | Source |
    |---|---|
    | `customer_id` | `customer_id` column |
    | `customer_name` | `customer_name` column |
    | `milestone_tier` | Static value: `gold` |
    | `total_spend` | `total_spend` column |
    | `store_id` | `store_id` column |
    | `detected_at` | Dynamic: current timestamp |

8. Select **Save** to activate the rule.

For full details on using Activator as a Business Events publisher, see the [Activator publisher documentation](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-activator).

## Step 3: Consumer 1 - Marketing reward (Activator → Power Automate)

1. In Real-Time Hub, locate `Retail.Customer.MilestoneReached` under your schema set.
2. Select **Set alert**.
3. Name the rule `Loyalty Milestone - Reward`.
4. In the **Monitor** section, set **Source** to **Business events** and connect to `Retail.Customer.MilestoneReached`.
5. Set **Condition** to `On each event`.
6. In the **Action** section, select **Power Automate** and connect the flow that sends the reward notification.
7. Add `customer_id`, `customer_name`, and `milestone_tier` as context fields.
8. Select **Save**.

## Step 4: Consumer 2 - Analytics (Eventhouse)

The Eventhouse consumer is enabled at Business Event creation time.

1. In Real-Time Hub, open `Retail.Customer.MilestoneReached`.
2. Select **Analyze in Eventhouse**.
3. Choose a destination KQL database and table name, for example `loyalty_milestones`.
4. Select **Save**.

All published events will be written to this table automatically.

## Step 5: Consumer 3 - CRM tier update (Activator → Power Automate)

Repeat the same steps as Consumer 1 with a different Power Automate flow:

1. In Real-Time Hub, locate `Retail.Customer.MilestoneReached`.
2. Select **Set alert**.
3. Name the rule `Loyalty Milestone - CRM Update`.
4. Set **Condition** to `On each event`.
5. Connect the Power Automate flow that updates the customer's loyalty tier in the CRM.
6. Add `customer_id` and `milestone_tier` as context fields.
7. Select **Save**.

## Step 6: End-to-end test

Trigger the flow manually by running the KQL query with a customer that meets the threshold. Then:

1. In Real-Time Hub, select `Retail.Customer.MilestoneReached`.
2. Go to the **Publisher** tab and confirm that your Activator rule is listed as an active publisher.
3. Go to the **Data preview** tab and verify that a matching event record appears.
4. Confirm that the marketing Power Automate flow fires and the reward notification is sent.
5. Confirm that the `loyalty_milestones` table in Eventhouse has a new row.
6. Confirm that the CRM Power Automate flow fires and the tier is updated.

## Why this matters

Each team controls their own subscription. Marketing can update the reward flow without involving analytics or CRM. Analytics can change the destination table without touching the detection rule. Adding a new team — a customer success manager who wants a Teams notification, for example — means creating one new subscription, nothing else.
