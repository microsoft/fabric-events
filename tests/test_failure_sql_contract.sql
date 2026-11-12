SET NOCOUNT ON;
DECLARE @pipeline nvarchar(128) = N'sql-contract-' + CONVERT(nvarchar(36), NEWID());
DECLARE @first nvarchar(128) = N'first', @second nvarchar(128) = N'second';
DECLARE @time nvarchar(64) = N'2026-01-01T10:00:00Z';

EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @first, N'failed', 12, 0, N'manual', @time;
DECLARE @id uniqueidentifier, @created datetime2(7);
SELECT @id = case_id, @created = created_at FROM dbo.PipelineFailureCases
WHERE pipeline_id = @pipeline AND run_id = @first;
IF @id IS NULL OR NOT EXISTS (
    SELECT 1 FROM dbo.PipelineFailureCases WHERE case_id = @id AND case_status = 'pending_triage'
        AND rows_processed = 12 AND duration_seconds = 0 AND completed_at = @time
)
    THROW 51200, 'A failed execution did not create its original SQL triage case.', 1;
UPDATE dbo.PipelineFailureCases SET case_status = 'in_progress' WHERE case_id = @id;
EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @first, N'failed', 12, 0, N'manual', @time;
IF NOT EXISTS (
    SELECT 1 FROM dbo.PipelineFailureCases WHERE case_id = @id AND created_at = @created
        AND case_status = 'in_progress'
)
    THROW 51201, 'An identical delivery changed the original identity or case lifecycle.', 1;
BEGIN TRY
    EXEC dbo.RecordPipelineFailure @pipeline, N'DAILY LOAD', @first, N'failed', 12, 0, N'manual', @time;
    THROW 51202, 'Conflicting case-only facts were accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51103 THROW;
END CATCH;
BEGIN TRY
    EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @second, N'completed', 12, 0, N'manual', @time;
    THROW 51203, 'A completed execution created a failure case.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51102 THROW;
END CATCH;
BEGIN TRY
    EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @second, N'skipped', 0, 0, N'manual', @time;
    THROW 51204, 'A skipped execution created a failure case.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51102 THROW;
END CATCH;
BEGIN TRY
    EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @second, N'failed', 0, -1, N'manual', @time;
    THROW 51205, 'A negative duration was accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51102 THROW;
END CATCH;
BEGIN TRY
    EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @second, N'failed', 0, 0, N'manual', N'2026-01-01T10:00:00';
    THROW 51206, 'An ambiguous completion time was accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51102 THROW;
END CATCH;
EXEC dbo.RecordPipelineFailure @pipeline, N'Daily load', @second, N'failed', 0, 0, N'manual', @time;
IF (SELECT COUNT(*) FROM dbo.PipelineFailureCases WHERE pipeline_id = @pipeline) <> 2
    THROW 51207, 'Two executions of one pipeline did not remain distinct cases.', 1;

DELETE dbo.PipelineFailureCases WHERE pipeline_id = @pipeline AND run_id IN (@first, @second);
IF EXISTS (SELECT 1 FROM dbo.PipelineFailureCases WHERE pipeline_id = @pipeline)
    THROW 51208, 'Exact synthetic contract cases remain after cleanup.', 1;
SELECT N'passed' AS sql_contract_result,
    N'create,duplicate,lifecycle,exact-strings,conflict,completed,skipped,negative,utc,distinct-runs' AS assertions;
