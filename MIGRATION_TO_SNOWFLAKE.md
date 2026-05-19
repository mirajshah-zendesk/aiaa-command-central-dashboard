# Migration to Snowflake

## Summary

The AIAA Command Central Dashboard has been updated to connect directly to Snowflake instead of requiring CSV file uploads.

## Changes Made

### 1. Data Source
- **Before**: CSV file upload via `st.file_uploader()`
- **After**: Direct Snowflake connection to `presentation.success.ai_agents_advanced_command_central`

### 2. Code Changes

**Added:**
- `load_data_from_snowflake()` function with 1-hour caching
- Snowflake Snowpark session management
- Auto-load on first visit
- Manual refresh button

**Removed:**
- CSV file uploader
- Manual CSV parsing logic

**Updated:**
- Sidebar UI: Now shows Snowflake connection status
- Welcome screen: References live Snowflake data instead of CSV
- Column mapping: `60+_DAY_TENURE` → `60+ Day Tenure?`

### 3. Dependencies

**pyproject.toml:**
```toml
dependencies = [
    "snowflake-snowpark-python>=1.11.0",  # Was: snowflake-connector-python
    "streamlit>=1.53.0",
]
```

### 4. Benefits

✅ **Live Data**: No more static CSV snapshots  
✅ **Resolves Discrepancies**: Should eliminate the 2 remaining metric mismatches caused by CSV/Excel timing differences  
✅ **Better UX**: Auto-loads on visit, one-click refresh  
✅ **Performance**: 1-hour caching reduces Snowflake queries  
✅ **Snowflake Native**: Runs seamlessly in Snowflake Streamlit environment

## Expected Impact on Validation

The dashboard previously matched **24/26 metrics (92%)** against Excel. The 2 discrepancies were:

1. **Projected Go-Live Instances**: -1 instance
2. **Bot Deployed Share %**: -2.88 percentage points

**Root cause identified**: CSV export was a static snapshot while Excel Scorecard tab connected to live Snowflake data.

**Expected outcome**: With live Snowflake data, these discrepancies should be resolved, achieving **26/26 metrics (100%)** match.

## Deployment

### To Snowflake (Recommended)
```bash
snow streamlit deploy --replace
```

### Local Testing
```bash
uv sync
uv run streamlit run streamlit_app.py
```

Note: Local mode requires Snowflake credentials and table access.

## Files Modified

- `streamlit_app.py`: Core logic updated for Snowflake connection
- `pyproject.toml`: Dependency update
- `README.md`: Documentation updated
- `MIGRATION_TO_SNOWFLAKE.md`: This file (new)

## Validation Checklist

After deployment, verify:

- [ ] Data loads automatically on first visit
- [ ] "Load Data from Snowflake" button refreshes data
- [ ] All 26 metrics calculate correctly
- [ ] Date range matches latest table refresh
- [ ] Filters (date, region) work as expected
- [ ] Performance is acceptable with caching
- [ ] Compare against Excel scorecard for 100% match

## Rollback Plan

If needed, revert to CSV version:
```bash
git checkout HEAD~1 streamlit_app.py pyproject.toml
```

The CSV-based version is stable and validated at 92% accuracy.
