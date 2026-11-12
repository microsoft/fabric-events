CREATE PROCEDURE dbo.RecordPipelineFailure
    @pipeline_id nvarchar(max),
    @pipeline_name nvarchar(max),
    @run_id nvarchar(max),
    @status nvarchar(max),
    @rows_processed int,
    @duration_seconds int,
    @triggered_by nvarchar(max),
    @completed_at nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT <> 0
        THROW 51101, 'Invoke case registration without an ambient transaction.', 1;
    IF @pipeline_id IS NULL OR LEN(@pipeline_id) = 0 OR DATALENGTH(@pipeline_id) > 256
        OR @pipeline_name IS NULL OR LEN(@pipeline_name) = 0 OR DATALENGTH(@pipeline_name) > 512
        OR @run_id IS NULL OR LEN(@run_id) = 0 OR DATALENGTH(@run_id) > 256
        OR @status IS NULL OR CONVERT(varbinary(max), @status) <> CONVERT(varbinary(max), N'failed')
        OR @rows_processed IS NULL OR @rows_processed < 0
        OR @duration_seconds IS NULL OR @duration_seconds < 0
        OR @triggered_by IS NULL OR CONVERT(varbinary(max), @triggered_by) NOT IN (
            CONVERT(varbinary(max), N'schedule'), CONVERT(varbinary(max), N'manual'), CONVERT(varbinary(max), N'upstream'))
        OR DATALENGTH(@triggered_by) > 64
        OR @completed_at IS NULL OR LEN(@completed_at) = 0 OR DATALENGTH(@completed_at) > 128
        OR TRY_CONVERT(datetimeoffset, @completed_at, 127) IS NULL
        OR (RIGHT(@completed_at, 1) COLLATE Latin1_General_100_BIN2 <> N'Z' AND RIGHT(@completed_at, 6) <> N'+00:00')
        OR DATEPART(TZOFFSET, TRY_CONVERT(datetimeoffset, @completed_at, 127)) <> 0
        THROW 51102, 'Provide the original failed execution facts and explicit UTC completion time.', 1;

    DECLARE @case_id uniqueidentifier;
    BEGIN TRY
        BEGIN TRANSACTION;
        SELECT @case_id = case_id FROM dbo.PipelineFailureCases WITH (UPDLOCK, HOLDLOCK)
        WHERE pipeline_id = @pipeline_id AND run_id = @run_id;
        IF @case_id IS NOT NULL
        BEGIN
            IF EXISTS (
                SELECT 1 FROM dbo.PipelineFailureCases
                WHERE case_id = @case_id AND (
                    CONVERT(varbinary(max), pipeline_id) <> CONVERT(varbinary(max), @pipeline_id)
                    OR CONVERT(varbinary(max), pipeline_name) <> CONVERT(varbinary(max), @pipeline_name)
                    OR CONVERT(varbinary(max), run_id) <> CONVERT(varbinary(max), @run_id)
                    OR CONVERT(varbinary(max), status) <> CONVERT(varbinary(max), @status)
                    OR rows_processed <> @rows_processed OR duration_seconds <> @duration_seconds
                    OR CONVERT(varbinary(max), triggered_by) <> CONVERT(varbinary(max), @triggered_by)
                    OR CONVERT(varbinary(max), completed_at) <> CONVERT(varbinary(max), @completed_at)
                )
            )
                THROW 51103, 'The execution has conflicting facts. No case was changed.', 1;
        END
        ELSE
        BEGIN
            SET @case_id = NEWID();
            INSERT dbo.PipelineFailureCases (
                case_id, pipeline_id, pipeline_name, run_id, status, rows_processed,
                duration_seconds, triggered_by, completed_at
            )
            VALUES (
                @case_id, @pipeline_id, @pipeline_name, @run_id, @status, @rows_processed,
                @duration_seconds, @triggered_by, @completed_at
            );
        END;
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
    SELECT case_id, pipeline_id, pipeline_name, run_id, status, rows_processed,
           duration_seconds, triggered_by, completed_at, case_status, created_at
    FROM dbo.PipelineFailureCases WHERE case_id = @case_id;
END;
