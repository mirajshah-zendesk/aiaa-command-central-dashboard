#!/usr/bin/env bash
# Deploy the Streamlit app to Snowflake.
# Run from the repo root: ./deploy.sh
set -euo pipefail

snow streamlit deploy --replace \
  -c default \
  --database STREAMLIT_APPS \
  --schema AIAA_COMMAND_CENTRAL \
  --role STREAMLIT_APP_ADMIN_ROLE
