-- Check if the notes table exists and what data is in it
-- Run this in Snowflake to debug the notes issue

-- 1. Check if table exists
SHOW TABLES LIKE 'ADOPTION_LOSS_NOTES' IN STREAMLIT_APPS.AIAA_COMMAND_CENTRAL;

-- 2. Check all notes in the table
SELECT *
FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
ORDER BY UPDATED_AT DESC;

-- 3. Check for notes with "Litera" in the name
SELECT *
FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
WHERE CRM_ACCOUNT_NAME ILIKE '%Litera%'
ORDER BY UPDATED_AT DESC;

-- 4. Check distinct snapshot dates in the notes table
SELECT DISTINCT SNAPSHOT_DATE, COUNT(*) as note_count
FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
GROUP BY SNAPSHOT_DATE
ORDER BY SNAPSHOT_DATE DESC;

-- 5. Check what CRM_ACCOUNT_IDs are in the adoption loss for latest snapshot
-- (You'll need to adjust the date to match your latest snapshot)
-- This helps verify if the CRM_ACCOUNT_ID matches what we're trying to save
