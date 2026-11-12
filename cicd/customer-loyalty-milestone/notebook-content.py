# Fabric notebook source

# METADATA ********************
# META {"kernel_info":{"name":"synapse_pyspark"},"dependencies":{}}

# CELL ********************

# ${catalog_helpers}

# CELL ********************

catalog = validate_catalog(${catalog_literal})
catalog_path = (
    "abfss://${workspace_id}@onelake.dfs.fabric.microsoft.com/"
    "${lakehouse_id}/Tables/LoyaltyCampaignCatalog"
)

catalog_frame = spark.createDataFrame(
    catalog,
    "catalog_tier string, catalog_store_id string, campaign_id string, benefit_code string, is_active boolean",
)
(
    catalog_frame.write.format("delta")
    .mode("errorifexists")
    .option("delta.columnMapping.mode", "none")
    .option("delta.enableDeletionVectors", "false")
    .save(catalog_path)
)
display(spark.read.format("delta").load(catalog_path))
