Fabric Business Events / Prototype card: revenue

# Revenue below target

[Back to the scenario](../revenue-below-target.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/revenue-below-target)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\revenue-below-target`; for an extracted package, open that scenario folder instead.

The [source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/revenue-below-target) contains the implementation seed. This site publishes the complete runbook and links to the implementation; it does not offer a complete deployable download.

**Contract and measure seed, not yet deployable or live-validated.** This deployment guide is the documentation source of truth. No empty pipeline, fabricated publisher definition or success-only deployment script stands in for the card.

## Card contract

Power BI semantic model/report -> Activator publisher -> `Sales.Revenue.BelowTarget` -> independent Activator and Eventhouse consumers. Activator starts a Teams notification and a real Data Factory investigation-data pipeline independently.

Default review names: `RevenueSchema`, `RevenuePublisher`, `RevenueActions`, `RevenueHistory` and `RevenueAnalytics`. The history table follows the event name: `['Sales.Revenue.BelowTarget']`. These are proposed names, not deployed resources. The Power BI source and pipeline are explicit dependencies, not hidden replacements for resources in the wizard.

## Implemented source

[schema.avsc](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/schema.avsc) contains exactly the card's six v1 fields. [scenario.json](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/scenario.json) specifies the publisher, field mapping, independent responses, proposed names and unresolved source/action bindings. [variance_pct.dax](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/publisher/variance_pct.dax) is the canonical numeric measure for a `RevenuePerformance` table with `region`, `period_start`, `revenue` and `target`.

The measure compares revenue and target in the same region, period and currency context. It returns `-20`, not `-0.20`, for revenue 80,000 against target 100,000. Do not apply percent formatting that multiplies the value again. Zero or negative targets yield no valid variance.

The native publisher must monitor `variance_pct` with **Becomes less than -15**. Exactly -15 does not publish. Staying below the threshold is not another crossing; recover above it before testing a second crossing. The source report's refresh and Activator evaluation cadence must both be observed. Python must not evaluate the condition and publish an event on Activator's behalf.

## Bindings required before deployment

Supply the genuine semantic-model workspace/model IDs, report/page/visual binding and native publisher definition. Bind all six event fields, including a dynamic UTC `detected_at`. Do not substitute a Notebook or KQL detector for the card's Power BI source.

Bind an approved Teams destination and a real investigation-data pipeline, including its workspace, item ID and actual parameter mapping. The pipeline must refresh useful investigation data; it must not be empty or modify the test report's revenue or target values. Keep its Activator consumer rule separate from the Teams rule and from the source detector.

Complete native schema-set creation, roles, Eventhouse association and consumer readback. Only then add root-level `deploy.bat` for preparing the environment and separate `test.bat` for sample input changes and evidence verification. Use fresh deployment identities and ignored scenario-local `.deployments`, without automatic recovery or adoption. The prototype configuration download remains disabled for this unfinished package.

## External test sequence and evidence

[data/test-cases.json](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/data/test-cases.json) starts at -10, observes exactly -15 without publication, then crosses to -20. It also covers remaining below, recovery and another crossing. Invalid target cases are separate. These are expected test vectors, not evidence that DAX or the native detector has run.

Use a fresh synthetic region or isolated test period on every run. Apply one external source change, refresh the report and confirm publisher evaluation before advancing. Preserve the region, period and currency filter context. For each crossing, independently verify the full Eventhouse payload, the actual Teams notification and the completed investigation-data pipeline run. A published event, saved rule or pipeline invocation alone proves none of the other outcomes.

Reference: [canonical revenue scenario](../revenue-below-target.md).

## Continue reading

- [Full scenario and manual setup](../revenue-below-target.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
