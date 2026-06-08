-- Create table for storing notes about customers with missed target deployment dates
-- This table is used by the AIAA Command Central Dashboard "Missed Target Dates" tab

CREATE TABLE IF NOT EXISTS STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.MISSED_TARGETS_NOTES (
    CRM_ACCOUNT_ID VARCHAR(255) NOT NULL COMMENT 'CRM account ID from Salesforce',
    CRM_ACCOUNT_NAME VARCHAR(500) COMMENT 'CRM account name from Salesforce',
    INSTANCE_ACCOUNT_ID VARCHAR(255) COMMENT 'Instance account ID',
    INSTANCE_NAME VARCHAR(500) COMMENT 'Instance subdomain name',
    SNAPSHOT_DATE DATE NOT NULL COMMENT 'The snapshot date for which this note applies',
    NOTES TEXT COMMENT 'User-entered notes about why the target date was missed and follow-up actions',
    CREATED_BY VARCHAR(255) COMMENT 'Email of the user who created this note',
    CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP() COMMENT 'Timestamp when the note was first created',
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP() COMMENT 'Timestamp when the note was last updated',
    PRIMARY KEY (CRM_ACCOUNT_ID, SNAPSHOT_DATE)
) COMMENT = 'Stores notes about customers who missed their target deployment dates.
One row per CRM account per snapshot date. Notes are shared across all users of the dashboard.';

-- Grant permissions to the Streamlit app admin role
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.MISSED_TARGETS_NOTES
TO ROLE STREAMLIT_APP_ADMIN_ROLE;

-- Grant read permissions to viewers
GRANT SELECT ON TABLE STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.MISSED_TARGETS_NOTES
TO ROLE STREAMLIT_APP_VIEWER_ROLE;
