SET NOCOUNT ON;
DECLARE @cart nvarchar(128) = N'sql-contract-' + CONVERT(nvarchar(36), NEWID());
DECLARE @boundary nvarchar(128) = @cart + N'-boundary';
DECLARE @zero nvarchar(128) = @cart + N'-zero';
DECLARE @time nvarchar(64) = N'2026-01-01T10:00:00Z';

EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'approved@example.invalid',
    @cart, 30.01, 2, @time, N'activation-original';
DECLARE @id uniqueidentifier, @created datetime2(7);
SELECT @id = task_id, @created = created_at FROM dbo.CartRecoveryTasks WHERE cart_id = @cart;
IF @id IS NULL OR NOT EXISTS (
    SELECT 1 FROM dbo.CartRecoveryTasks
    WHERE cart_id = @cart AND customer_id = N'test-cart-a'
        AND customer_email = N'approved@example.invalid' AND cart_value = CAST(30.01 AS real)
        AND item_count = 2 AND last_activity_at = @time AND status = 'pending'
        AND first_activation_id = N'activation-original' AND created_at <= SYSUTCDATETIME()
)
    THROW 51100, 'A new task did not preserve the original cart facts and actual persisted state.', 1;

UPDATE dbo.CartRecoveryTasks SET status = 'in_progress' WHERE task_id = @id;
EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'approved@example.invalid',
    @cart, 30.01, 2, @time, N'activation-retry';
IF (SELECT COUNT(*) FROM dbo.CartRecoveryTasks WHERE cart_id = @cart) <> 1
    OR NOT EXISTS (
        SELECT 1 FROM dbo.CartRecoveryTasks
        WHERE task_id = @id AND cart_id = @cart AND created_at = @created
            AND status = 'in_progress' AND first_activation_id = N'activation-original'
    )
    THROW 51101, 'An identical retry changed the original task identity or lifecycle.', 1;

BEGIN TRY
    EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'approved@example.invalid',
        @cart, 30.02, 2, @time, N'activation-conflict';
    THROW 51102, 'Conflicting facts were accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51004 THROW;
END CATCH;
BEGIN TRY
    EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'APPROVED@example.invalid',
        @cart, 30.01, 2, @time, N'activation-case-conflict';
    THROW 51103, 'A case-only change in the original email was accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51004 THROW;
END CATCH;
BEGIN TRY
    EXEC dbo.RecordCartRecoveryTask N'unknown-customer', N'approved@example.invalid',
        @zero, 0, 1, @time, N'activation-unknown';
    THROW 51104, 'An unregistered customer was accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51003 THROW;
END CATCH;
IF EXISTS (SELECT 1 FROM dbo.CartRecoveryTasks WHERE cart_id = @zero)
    THROW 51105, 'A rejected operation persisted a task.', 1;

BEGIN TRY
    EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'approved@example.invalid',
        @zero, -1, 1, @time, N'activation-invalid';
    THROW 51106, 'An invalid cart value was accepted.', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51002 THROW;
END CATCH;

EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'approved@example.invalid',
    @boundary, 30, 1, @time, N'activation-boundary';
EXEC dbo.RecordCartRecoveryTask N'test-cart-a', N'approved@example.invalid',
    @zero, 0, 1, @time, N'activation-zero';
IF (SELECT COUNT(*) FROM dbo.CartRecoveryTasks WHERE cart_id IN (@cart, @boundary, @zero)) <> 3
    OR NOT EXISTS (
        SELECT 1 FROM dbo.CartRecoveryTasks WHERE task_id = @id
            AND cart_value = CAST(30.01 AS real) AND status = 'in_progress'
            AND created_at = @created AND first_activation_id = N'activation-original'
    )
    THROW 51107, 'Distinct carts or original task state were not retained.', 1;

DELETE dbo.CartRecoveryTasks WHERE cart_id IN (@cart, @boundary, @zero);
IF EXISTS (SELECT 1 FROM dbo.CartRecoveryTasks WHERE cart_id IN (@cart, @boundary, @zero))
    THROW 51108, 'The exact synthetic SQL contract records remain after cleanup.', 1;
SELECT N'passed' AS sql_contract_result,
    N'create,duplicate,conflict,exact-strings,unknown-customer,invalid-value,boundary,zero' AS assertions;
