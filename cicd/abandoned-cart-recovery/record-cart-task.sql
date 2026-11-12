CREATE PROCEDURE dbo.RecordCartRecoveryTask
    @customer_id nvarchar(max),
    @customer_email nvarchar(max),
    @cart_id nvarchar(max),
    @cart_value real,
    @item_count int,
    @last_activity_at nvarchar(max),
    @activation_id nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @@TRANCOUNT <> 0
        THROW 51001, 'Invoke the follow-up operation without an ambient transaction.', 1;
    IF @customer_id IS NULL OR LEN(@customer_id) = 0 OR DATALENGTH(@customer_id) > 256
        OR @cart_id IS NULL OR LEN(@cart_id) = 0 OR DATALENGTH(@cart_id) > 256
        OR @customer_email IS NULL OR LEN(@customer_email) = 0 OR DATALENGTH(@customer_email) > 508
        OR @cart_value IS NULL OR @cart_value < 0 OR @cart_value > 1000000000000
        OR @item_count IS NULL OR @item_count < 1
        OR @last_activity_at IS NULL OR LEN(@last_activity_at) = 0
        OR @activation_id IS NULL OR LEN(@activation_id) = 0 OR DATALENGTH(@activation_id) > 512
        THROW 51002, 'Provide all six original cart fields and the actual activation identity.', 1;

    DECLARE @task_id uniqueidentifier;
    BEGIN TRY
        BEGIN TRANSACTION;
        IF NOT EXISTS (SELECT 1 FROM dbo.Customers WITH (HOLDLOCK) WHERE customer_id = @customer_id)
            THROW 51003, 'The supplied customer is not registered in this follow-up database.', 1;

        SELECT @task_id = task_id
        FROM dbo.CartRecoveryTasks WITH (UPDLOCK, HOLDLOCK)
        WHERE cart_id = @cart_id;

        IF @task_id IS NOT NULL
        BEGIN
            IF EXISTS (
                SELECT 1 FROM dbo.CartRecoveryTasks
                WHERE task_id = @task_id AND (
                    CONVERT(varbinary(max), cart_id) <> CONVERT(varbinary(max), @cart_id)
                    OR CONVERT(varbinary(max), customer_id) <> CONVERT(varbinary(max), @customer_id)
                    OR CONVERT(varbinary(max), customer_email) <> CONVERT(varbinary(max), @customer_email)
                    OR cart_value <> @cart_value OR item_count <> @item_count
                    OR CONVERT(varbinary(max), last_activity_at) <> CONVERT(varbinary(max), @last_activity_at)
                )
            )
                THROW 51004, 'The existing cart has conflicting facts. No task was changed.', 1;
        END
        ELSE
        BEGIN
            SET @task_id = NEWID();
            INSERT dbo.CartRecoveryTasks (
                task_id, cart_id, customer_id, customer_email, cart_value, item_count,
                last_activity_at, status, first_activation_id, created_at
            )
            VALUES (
                @task_id, @cart_id, @customer_id, @customer_email, @cart_value, @item_count,
                @last_activity_at, 'pending', @activation_id, SYSUTCDATETIME()
            );
        END;
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;

    SELECT task_id, cart_id, customer_id, customer_email, cart_value, item_count,
           last_activity_at, status, first_activation_id, created_at
    FROM dbo.CartRecoveryTasks
    WHERE task_id = @task_id;
END;
