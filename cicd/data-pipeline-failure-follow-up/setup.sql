SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.PipelineFailureCases', N'U') IS NOT NULL
    THROW 51100, 'The case table already exists. Inspect it instead of replacing its data.', 1;

CREATE TABLE dbo.PipelineFailureCases (
    case_id uniqueidentifier NOT NULL CONSTRAINT DF_PipelineFailureCases_Id DEFAULT NEWID(),
    pipeline_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    pipeline_name nvarchar(256) NOT NULL,
    run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    status nvarchar(16) NOT NULL CONSTRAINT CK_PipelineFailureCases_Failed CHECK (status = N'failed'),
    rows_processed int NOT NULL CONSTRAINT CK_PipelineFailureCases_Rows CHECK (rows_processed >= 0),
    duration_seconds int NOT NULL CONSTRAINT CK_PipelineFailureCases_Duration CHECK (duration_seconds >= 0),
    triggered_by nvarchar(32) NOT NULL,
    completed_at nvarchar(64) NOT NULL,
    case_status varchar(24) NOT NULL CONSTRAINT DF_PipelineFailureCases_Status DEFAULT 'pending_triage'
        CONSTRAINT CK_PipelineFailureCases_Lifecycle CHECK (case_status IN ('pending_triage', 'in_progress', 'resolved')),
    created_at datetime2(7) NOT NULL CONSTRAINT DF_PipelineFailureCases_Created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_PipelineFailureCases PRIMARY KEY (case_id),
    CONSTRAINT UQ_PipelineFailureCases_Run UNIQUE (pipeline_id, run_id)
);
