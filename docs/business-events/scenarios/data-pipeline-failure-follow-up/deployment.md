# Data Pipeline Failure Follow-up: Workbook

[Business context and architecture](../data-pipeline-failure-follow-up.md) | [Download package](../../../assets/downloads/data-pipeline-failure-follow-up.zip) | [Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/data-pipeline-failure-follow-up)

Give the operations team a follow-up case when a data workload fails, without adding successful or skipped work to its queue. Prepare the case table, then run one Notebook sample with six workload examples. Only the two failures should need triage.

<span id="data-pipeline-failure-follow-up-deployment-guide"></span>
<span id="deployment-architecture"></span>

## Resources and coverage

| Component | Default name | Package coverage |
|---|---|---|
| Event schema and delegated permissions | `DataOpsSchema` | Original eight-field `DataOps.Pipeline.RunCompleted` contract |
| Notebook publisher | `DataOpsPublisher` | Shares the outcome of each sample workload: completed, failed or skipped |
| Activator (User Data Function) | `DataOpsActions` | Active **Register failed run** rule; source filter `status == "failed"` and **On each value** |
| Response User Data Function | `DataOpsFollowUp` | Eight dynamic parameters and the managed `FailureCases` SQL connection |
| SQL database | `DataOpsCases` | Database provisioned; execute the two SQL scripts manually before testing |

The package creates the five items above. Fabric also creates the database's read-only SQL analytics endpoint; use the writable SQL database for setup and cases. The response registers a case; it does not retry a load, assign an owner or repair data.

## Before you start

Use Python 3.10+, Azure CLI and PowerShell on Windows, an active supported Fabric capacity, and permission to create the scenario's workspace items and execute its source. Prepare any learner-owned connections listed in the coverage table.

## 1. Configure your environment

Download **`data-pipeline-failure-follow-up.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\data-pipeline-failure-follow-up` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\data-pipeline-failure-follow-up'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Use Python 3.10 or later, Azure CLI, an active supported Fabric capacity, and an account authorized to create items, run the Notebook and execute the response function. The SQL database owner prepares the tables. The response identity needs database access and `EXECUTE` on `dbo.RecordPipelineFailure`; the person checking cases needs `SELECT` on `dbo.PipelineFailureCases`.

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` if you do not accept preview APIs, which prevents deployment. Dynamic Fabric item action parameters are a Preview capability. Configuration does not authenticate, deploy or replace an existing configuration.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Open the configured workspace and find the schema, Notebook, Activator, response UDF and SQL database. The rule, SQL connection and eight dynamic mappings are prepared. Continue with the SQL setup before sending the sample.

## 3. Initialize the case table before testing

1. Open **DataOpsCases**, of type **SQL database**, not its SQL analytics endpoint.
2. In the SQL query editor, paste and execute `setup.sql` once. It creates `dbo.PipelineFailureCases`.
3. In a separate new query, paste and execute `record-pipeline-failure.sql`. It creates `dbo.RecordPipelineFailure`.
4. Run `SELECT * FROM dbo.PipelineFailureCases;` and confirm zero initial cases. Existing objects are not overwritten.
5. Open **DataOpsFollowUp → Manage connections** and inspect the deployed `FailureCases` alias. It must reference this SQL database. Do not create a replacement connection or change the alias.
6. Open **DataOpsActions → Register failed run**. Inspect the `failed` source filter, **On each value**, the `register_pipeline_failure` target and eight dynamic mappings:

    | Function parameter | Event field | Type |
    |---|---|---|
    | `pipelineId` | `pipeline_id` | String |
    | `pipelineName` | `pipeline_name` | String |
    | `runId` | `run_id` | String |
    | `status` | `status` | String |
    | `rowsProcessed` | `rows_processed` | Number |
    | `durationSeconds` | `duration_seconds` | Number |
    | `triggeredBy` | `triggered_by` | String |
    | `completedAt` | `completed_at` | String |

The rule and connection are already prepared; keep their settings unchanged. The command's `sql_destination` output contains the database server and catalog if you need them.

## 4. Run workloads and inspect follow-up cases

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Run the sample once. It includes six workload examples in a single Notebook run; only the two failed workloads should create follow-up cases.

### What you should see

Wait for the Notebook run to reach `Completed`, then check the responses and cases below.

The **Workload** values are case names, not statuses. For example, `empty-completed` and `another-completed` both produce `status: "completed"`. The event statuses are `failed`, `completed` and `skipped`.

| Workload | Expected status | Rows processed | Expected SQL case |
|---|---|---|---|
| failed-load | `failed` | `12` | `pending_triage` |
| failed-next-run | `failed` | `3` | `pending_triage` |
| completed | `completed` | `25` | None: ignored |
| empty-completed | `completed` | `0` | None: ignored |
| another-completed | `completed` | `8` | None: ignored |
| skipped | `skipped` | `0` | None: ignored |

Only the two `failed` outcomes match **Register failed run**. Expect two rule activations, two matching `DataOpsFollowUp` executions and two SQL cases initially marked `pending_triage`. Their `rows_processed` values are `12` and `3`: each load failed after processing that many rows.

The three `completed` outcomes and the one `skipped` outcome are ignored by the Activator source filter, so they should not create responses or SQL cases. A successful workload that processes zero rows is still completed, not failed. Their absence from the follow-up table is expected: the queue contains only work that needs attention.

### Evaluation criteria

1. Confirm that the Notebook run reached `Completed`.
2. In **Register failed run** and `DataOpsFollowUp`, expect two successful responses, one for each failure. Completed and skipped workloads should have no response.
3. Open **DataOpsCases**, of type **SQL database**, and query `dbo.PipelineFailureCases` as shown below. Use the Notebook item ID returned as `items.notebook` by the test command. The table was empty before this test; expect exactly two rows for this Notebook.
4. Check that both cases have `status = "failed"` and `case_status = "pending_triage"`, with `rows_processed` values `12` and `3`. Each case should retain its workload details and completion time. Both belong to the same Notebook, but their different `run_id` and `case_id` values identify two failures requiring separate follow-up.
5. Observe the same run for at least five minutes without submitting it again. The table must remain at two cases, with no cases or response executions for `completed` or `skipped`.

```sql
SELECT case_id, pipeline_id, pipeline_name, run_id, status, rows_processed,
       duration_seconds, triggered_by, completed_at, case_status, created_at
FROM dbo.PipelineFailureCases
WHERE pipeline_id = N'<source-notebook-item-id>'
ORDER BY created_at, run_id;
```

The two cases are ready for triage. The test does not retry the failed loads or repair their data.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['DataOps.Pipeline.RunCompleted']
    | order by ingestion_time() desc
    | project pipeline_id, pipeline_name, run_id, status, rows_processed,
        duration_seconds, triggered_by, completed_at
    | take 50
    ```

    ```kusto
    ['DataOps.Pipeline.RunCompleted']
    | where ingestion_time() > ago(30d) and status == 'failed'
    | summarize ReceivedFailureEvents = count(), DistinctRuns = dcount(run_id) by pipeline_id, pipeline_name
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `DataOpsSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "DataOps.Pipeline.RunCompleted",
      "fields": [
        {"name": "pipeline_id", "type": "string"},
        {"name": "pipeline_name", "type": "string"},
        {"name": "run_id", "type": "string"},
        {"name": "status", "type": "string"},
        {"name": "rows_processed", "type": "int"},
        {"name": "duration_seconds", "type": "int"},
        {"name": "triggered_by", "type": "string"},
        {"name": "completed_at", "type": "string"}
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    import datetime
    import logging
    import time
    import uuid
    import notebookutils

    WORKSPACE_NAME = "<your-workspace-name>"
    SCHEMA_SET_NAME = "DataOps"
    PIPELINE_ID = "<your-notebook-item-id>"
    PIPELINE_NAME = "DataOpsPublisher"


    class SkippedWorkload(Exception):
        pass


    class SyntheticLoadFailure(Exception):
        pass


    def publish_outcome(event):
        accepted = notebookutils.businessEvents.publish(
            eventSchemaSetWorkspace=WORKSPACE_NAME,
            eventSchemaSet=SCHEMA_SET_NAME,
            eventTypeName="DataOps.Pipeline.RunCompleted",
            eventData=event,
            dataVersion="v1",
        )
        if accepted is not True:
            raise RuntimeError("The workload outcome was not accepted for publication.")
        print(event)


    def execute_workload(work, run_id):
        progress = {"rows_processed": 0}
        started = time.monotonic()
        status = "completed"
        try:
            work(progress)
        except SkippedWorkload:
            status = "skipped"
            logging.info("Workload %s was explicitly skipped.", run_id)
        except Exception:
            status = "failed"
            logging.exception("Workload %s failed.", run_id)
            raise
        finally:
            publish_outcome(validate_event({
                "pipeline_id": PIPELINE_ID,
                "pipeline_name": PIPELINE_NAME,
                "run_id": run_id,
                "status": status,
                "rows_processed": progress["rows_processed"],
                "duration_seconds": int(time.monotonic() - started),
                "triggered_by": "manual",
                "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            }))
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    ```python
    import datetime
    import uuid
    import fabric.functions as fn

    udf = fn.UserDataFunctions()


    @udf.connection(argName="caseDatabase", alias="FailureCases")
    @udf.function()
    def register_pipeline_failure(
        caseDatabase: fn.FabricSqlConnection,
        pipelineId: str, pipelineName: str, runId: str, status: str,
        rowsProcessed: int, durationSeconds: int, triggeredBy: str, completedAt: str,
    ) -> dict:
        event = validate_event({
            "pipeline_id": pipelineId, "pipeline_name": pipelineName, "run_id": runId,
            "status": status, "rows_processed": rowsProcessed, "duration_seconds": durationSeconds,
            "triggered_by": triggeredBy, "completed_at": completedAt,
        })
        if status != "failed":
            raise ValueError("Only failed executions may reach case registration.")
        connection = caseDatabase.connect()
        try:
            connection.autocommit = True
            cursor = connection.cursor()
            cursor.execute(
                "EXEC dbo.RecordPipelineFailure @pipeline_id=?, @pipeline_name=?, @run_id=?, @status=?, "
                "@rows_processed=?, @duration_seconds=?, @triggered_by=?, @completed_at=?",
                *[event[name] for name in FIELDS],
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Case registration returned no persisted row.")
            result = dict(zip([column[0] for column in cursor.description], row))
            if (any(type(result.get(name)) is not type(value) or result[name] != value for name, value in event.items())
                    or result.get("case_status") not in ("pending_triage", "in_progress", "resolved")):
                raise RuntimeError("The persisted case does not match the failed execution.")
            result["case_id"] = str(uuid.UUID(str(result["case_id"])))
            created = result["created_at"]
            if not isinstance(created, datetime.datetime) or created.tzinfo is not None:
                raise RuntimeError("SQL did not return the expected UTC datetime2 creation time.")
            result["created_at"] = created.replace(tzinfo=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            return result
        finally:
            connection.close()
    ```

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    ```python
    source_run_id = uuid.uuid4()
    fixtures = [
        ("failed-load", "fail", 12),
        ("failed-next-run", "fail", 3),
        ("completed", "complete", 25),
        ("empty-completed", "complete", 0),
        ("another-completed", "complete", 8),
        ("skipped", "skip", 0),
    ]
    for label, operation, rows in fixtures:
        def work(progress):
            if operation == "skip":
                raise SkippedWorkload("Synthetic workload was not scheduled to run.")
            for _ in range(rows):
                progress["rows_processed"] += 1
            if operation == "fail":
                raise SyntheticLoadFailure("Controlled synthetic load failure.")
        try:
            execute_workload(work, str(uuid.uuid5(source_run_id, label)))
        except SyntheticLoadFailure:
            if operation != "fail":
                raise
            logging.info("Continuing after the expected synthetic failure.")
    print("Source run:", source_run_id)
    ```

    ```sql
    SELECT case_id, pipeline_id, pipeline_name, run_id, status, rows_processed,
           duration_seconds, triggered_by, completed_at, case_status, created_at
    FROM dbo.PipelineFailureCases
    WHERE pipeline_id = N'<your-notebook-item-id>'
      AND run_id IN (N'<failed-load-run-id>', N'<failed-next-run-id>');
    ```

    | Workload | Expected event status | Rows processed | Expected SQL case |
    |---|---|---|---|
    | failed-load | `failed` | `12` | `pending_triage` |
    | failed-next-run | `failed` | `3` | `pending_triage` |
    | completed | `completed` | `25` | None |
    | empty-completed | `completed` | `0` | None |
    | another-completed | `completed` | `8` | None |
    | skipped | `skipped` | `0` | None |

## Further reading

- [`failure_records.py`](https://github.com/microsoft/fabric-events/blob/main/cicd/failure_records.py)
- [publishing Business Events from a Notebook](https://learn.microsoft.com/fabric/real-time-hub/publish-business-events)
- [`setup.sql`](https://github.com/microsoft/fabric-events/blob/main/cicd/data-pipeline-failure-follow-up/setup.sql)
- [`record-pipeline-failure.sql`](https://github.com/microsoft/fabric-events/blob/main/cicd/data-pipeline-failure-follow-up/record-pipeline-failure.sql)
- [User Data Functions data connections](https://learn.microsoft.com/fabric/data-engineering/user-data-functions/connect-to-data-sources)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="connect-activator-to-the-failed-outcomes"></span>
<span id="data-pipeline-failure-follow-up"></span>
<span id="deploy-this-scenario"></span>
<span id="prepare-the-response-function"></span>
<span id="prepare-the-sql-case-store"></span>
<span id="preserve-failure-behavior"></span>
<span id="query-received-execution-history"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-notebook"></span>
<span id="step-3-consumers-sql-follow-up-and-execution-history"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
