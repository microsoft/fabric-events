SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;
    IF OBJECT_ID(N'dbo.Customers') IS NOT NULL OR OBJECT_ID(N'dbo.CartRecoveryTasks') IS NOT NULL
        THROW 51000, 'The follow-up tables already exist. Setup requires a fresh scenario database.', 1;

    CREATE TABLE dbo.Customers (
        customer_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL
            CONSTRAINT PK_Customers PRIMARY KEY,
        display_name nvarchar(128) NOT NULL
    );

    CREATE TABLE dbo.CartRecoveryTasks (
        task_id uniqueidentifier NOT NULL CONSTRAINT PK_CartRecoveryTasks PRIMARY KEY,
        cart_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL
            CONSTRAINT UQ_CartRecoveryTasks_Cart UNIQUE,
        customer_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL
            CONSTRAINT FK_CartRecoveryTasks_Customers REFERENCES dbo.Customers(customer_id),
        customer_email nvarchar(254) COLLATE Latin1_General_100_BIN2 NOT NULL,
        cart_value real NOT NULL CONSTRAINT CK_CartRecoveryTasks_Value CHECK (cart_value >= 0),
        item_count int NOT NULL CONSTRAINT CK_CartRecoveryTasks_Count CHECK (item_count > 0),
        last_activity_at nvarchar(max) COLLATE Latin1_General_100_BIN2 NOT NULL,
        status varchar(16) NOT NULL CONSTRAINT CK_CartRecoveryTasks_Status
            CHECK (status IN ('pending', 'in_progress', 'completed')),
        first_activation_id nvarchar(256) NOT NULL,
        created_at datetime2(7) NOT NULL
    );

    INSERT dbo.Customers (customer_id, display_name)
    VALUES (N'test-cart-a', N'Test customer A'), (N'test-cart-b', N'Test customer B');
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

SELECT customer_id, display_name FROM dbo.Customers ORDER BY customer_id;
SELECT COUNT(*) AS initial_task_count FROM dbo.CartRecoveryTasks;
