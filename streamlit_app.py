"""
AIAA Command Central Dashboard
Calculates all scorecard metrics from GlobalData matching Excel formulas
Now connects directly to Snowflake: presentation.success.ai_agents_advanced_command_central
"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from snowflake.snowpark.context import get_active_session

# Page config
st.set_page_config(
    page_title="AIAA Command Central",
    page_icon=":material/analytics:",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    div[data-testid="stMetricValue"] {
        font-size: 1.5rem;
    }
</style>
""", unsafe_allow_html=True)

st.title(":material/analytics: AIAA Command Central")

# Initialize session state
if "global_data" not in st.session_state:
    st.session_state.global_data = None
if "last_load_time" not in st.session_state:
    st.session_state.last_load_time = None

def init_notes_table():
    """Initialize the notes table if it doesn't exist"""
    try:
        session = get_active_session()

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES (
            CRM_ACCOUNT_ID VARCHAR(255),
            CRM_ACCOUNT_NAME VARCHAR(500),
            SNAPSHOT_DATE DATE,
            NOTES TEXT,
            CREATED_BY VARCHAR(255),
            CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            PRIMARY KEY (CRM_ACCOUNT_ID, SNAPSHOT_DATE)
        )
        """
        session.sql(create_table_sql).collect()
        return True
    except Exception as e:
        st.error(f"Error initializing notes table: {e}")
        return False

def save_note(crm_account_id, crm_account_name, snapshot_date, notes, user_email):
    """Save or update a note for a customer"""
    try:
        session = get_active_session()

        # Format date consistently
        if isinstance(snapshot_date, pd.Timestamp):
            snapshot_date_str = snapshot_date.strftime('%Y-%m-%d')
        else:
            snapshot_date_str = str(snapshot_date)

        # Create a temporary dataframe with the data to merge
        from snowflake.snowpark.functions import lit, current_timestamp

        temp_df = session.create_dataframe([{
            'CRM_ACCOUNT_ID': crm_account_id,
            'CRM_ACCOUNT_NAME': crm_account_name,
            'SNAPSHOT_DATE': snapshot_date_str,
            'NOTES': notes,
            'CREATED_BY': user_email
        }])

        # Write to a temporary stage or use direct merge
        # For now, let's try a simpler approach: check if exists, then update or insert

        # Check if record exists
        check_sql = f"""
        SELECT COUNT(*) as cnt
        FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
        WHERE CRM_ACCOUNT_ID = '{crm_account_id}'
          AND SNAPSHOT_DATE = '{snapshot_date_str}'::DATE
        """
        result = session.sql(check_sql).collect()
        exists = result[0]['CNT'] > 0

        if exists:
            # Update existing record
            update_sql = f"""
            UPDATE STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
            SET NOTES = $${notes}$$,
                UPDATED_AT = CURRENT_TIMESTAMP()
            WHERE CRM_ACCOUNT_ID = '{crm_account_id}'
              AND SNAPSHOT_DATE = '{snapshot_date_str}'::DATE
            """
            st.info(f"Executing UPDATE SQL (notes length: {len(notes)} chars)")
            st.code(update_sql, language="sql")
            session.sql(update_sql).collect()
        else:
            # Insert new record
            insert_sql = f"""
            INSERT INTO STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
            (CRM_ACCOUNT_ID, CRM_ACCOUNT_NAME, SNAPSHOT_DATE, NOTES, CREATED_BY, UPDATED_AT)
            VALUES (
                '{crm_account_id}',
                $${crm_account_name}$$,
                '{snapshot_date_str}'::DATE,
                $${notes}$$,
                '{user_email}',
                CURRENT_TIMESTAMP()
            )
            """
            st.info(f"Executing INSERT SQL (notes length: {len(notes)} chars)")
            st.code(insert_sql, language="sql")
            session.sql(insert_sql).collect()

        return True
    except Exception as e:
        st.error(f"Error saving note: {e}")
        import traceback
        st.error(traceback.format_exc())
        return False

def load_notes(snapshot_date):
    """Load all notes for a specific snapshot date"""
    try:
        session = get_active_session()

        # Format date consistently
        if isinstance(snapshot_date, pd.Timestamp):
            snapshot_date_str = snapshot_date.strftime('%Y-%m-%d')
        else:
            snapshot_date_str = str(snapshot_date)

        query = f"""
        SELECT CRM_ACCOUNT_ID, CRM_ACCOUNT_NAME, NOTES, CREATED_BY, UPDATED_AT
        FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
        WHERE SNAPSHOT_DATE = '{snapshot_date_str}'::DATE
        """
        df = session.sql(query).to_pandas()
        return df
    except Exception as e:
        # Table might not exist yet or other error
        st.warning(f"Could not load notes: {e}")
        return pd.DataFrame(columns=['CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME', 'NOTES', 'CREATED_BY', 'UPDATED_AT'])

@st.cache_data(ttl=3600)  # Cache for 1 hour
def load_data_from_snowflake():
    """Load data from Snowflake table"""
    try:
        session = get_active_session()

        # Query the table
        query = """
        SELECT *
        FROM presentation.success.ai_agents_advanced_command_central
        ORDER BY SOURCE_SNAPSHOT_DATE DESC
        """

        df = session.sql(query).to_pandas()

        # Rename 'TENURE_60_PLUS_DAYS' to '60+ Day Tenure?' to match Excel column naming
        if 'TENURE_60_PLUS_DAYS' in df.columns:
            df.rename(columns={'TENURE_60_PLUS_DAYS': '60+ Day Tenure?'}, inplace=True)
        # Fallback for old column name (backwards compatibility)
        elif '60+_day_tenure' in df.columns:
            df.rename(columns={'60+_day_tenure': '60+ Day Tenure?'}, inplace=True)

        return df, None
    except Exception as e:
        return None, str(e)

def clean_numeric_column(series):
    """Convert a series to numeric, handling errors gracefully"""
    return pd.to_numeric(series, errors='coerce').fillna(0)

def calculate_scorecard_metrics(df):
    """
    Calculate all scorecard metrics from GlobalData CSV
    Matches the Excel scorecard formulas exactly

    Key filters applied (matching Excel):
    - ARR Band: b) 12K-100K, c) 100K+ only
    - Uses '60+ Day Tenure?' column for eligible calculations
    - Grain: CRM for customer metrics, Instance for instance metrics
    """
    # Ensure date column is datetime
    df['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(df['SOURCE_SNAPSHOT_DATE'])

    # Clean numeric columns
    numeric_columns = [
        'AUTOMATED_RESOLUTIONS_PAID', 'BOT_INTERACTIONS_PAID', 'TOTAL_CREATED_TICKETS_28D',
        'EMAIL_AUTOMATED_RESOLUTIONS_PAID', 'MSG_AUTOMATED_RESOLUTIONS_PAID',
        'EMAIL_BOT_INTERACTIONS_PAID', 'MSG_BOT_INTERACTIONS_PAID',
        'EMAIL_AR_RATE_PAID', 'MSG_AR_RATE_PAID', 'AR_RATE_PAID',
        'OVERALL_BOT_DEPLOYED_SHARE', 'ACTIVE_INTEGRATIONS_28D',
        'TOP_BOX_5_STAR_28D', 'TOTAL_RESPONSES_28D', 'TENURE_MONTHS',
        'AUTOMATED_RESOLUTIONS_NET_ARR_USD', 'ALLOWANCE_PERIOD_MONTHS',
        'DAYS_INTO_ALLOWANCE_CYCLE', 'TOTAL_ALLOWANCE',
        'PRORATED_ALLOWANCE_LAST_28D', 'AUTOMATED_RESOLUTIONS_USED_LAST_28D_NORMALIZED',
        'VERIFIED_AUTOMATED_RESOLUTION_RATE_PAID'
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = clean_numeric_column(df[col])

    # NOTE: NO ARR band filter applied - the scorecard formulas don't filter by ARR band
    # The "b) 12K-100K, c) 100K+" label in Excel is just documentation, not a filter

    # Sort by date
    df = df.sort_values('SOURCE_SNAPSHOT_DATE')

    # Get unique dates
    dates = df['SOURCE_SNAPSHOT_DATE'].unique()

    metrics_list = []

    for date in dates:
        snapshot = df[df['SOURCE_SNAPSHOT_DATE'] == date].copy()

        # Calculate metrics for this snapshot
        metrics = {
            'Date': date,
        }

        # Impact Metrics - Customer counts (CRM level)
        # "# customers" = # penetrated customers (CRM grain)
        # Note: Data is already filtered by product type, just count unique CRM accounts
        metrics['# customers'] = snapshot['CRM_ACCOUNT_ID'].nunique()

        # "# instances" = # penetrated instances (Instance grain)
        # Note: Data is already filtered by product type, just count unique instances
        metrics['# instances'] = snapshot['INSTANCE_ACCOUNT_ID'].nunique()

        # Eligible (60+ day tenure) - Use '60+ Day Tenure?' column
        # This filter is used for both adopted AND eligible metrics
        eligible_tenure_filter = snapshot['60+ Day Tenure?'] == True

        # Adopted customers (CRM grain) - MUST have 60+ day tenure
        # Formula: Count customers where they're adopted for their product type
        # For Advanced: use ADVANCED_ADOPTED; For Essentials: use PAID_ADOPTED
        crm_adopted_filter = (
            ((snapshot['CRM_IS_AI_AGENTS_ADVANCED_ADOPTED'] == True) & (snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True)) |
            ((snapshot['CRM_IS_AI_AGENTS_PAID_ADOPTED'] == True) & (snapshot['CRM_IS_AI_AGENTS_PAID_PENETRATED'] == True) & (snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == False))
        ) & eligible_tenure_filter
        metrics['Adopted customers'] = snapshot[crm_adopted_filter]['CRM_ACCOUNT_ID'].nunique()

        # Adopted instances (Instance grain) - MUST have 60+ day tenure
        instance_adopted_filter = (
            ((snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_ADOPTED'] == True) & (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True)) |
            ((snapshot['INSTANCE_IS_AI_AGENTS_PAID_ADOPTED'] == True) & (snapshot['INSTANCE_IS_AI_AGENTS_PAID_PENETRATED'] == True) & (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == False))
        ) & eligible_tenure_filter
        metrics['Adopted instances'] = snapshot[instance_adopted_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # $100k+ ARR adopted customers
        adopted_100k_filter = crm_adopted_filter & (snapshot['CRM_ARR_BAND_BROAD'] == 'c) 100K+')
        metrics['Adopted customers ($100k+)'] = snapshot[adopted_100k_filter]['CRM_ACCOUNT_ID'].nunique()

        # Eligible customers (CRM grain, 60+ day tenure)
        # Note: Data already filtered by product type, just apply tenure filter
        eligible_cust_filter = eligible_tenure_filter
        metrics['Eligible customers'] = snapshot[eligible_cust_filter]['CRM_ACCOUNT_ID'].nunique()

        # Eligible customers ($100k+)
        eligible_100k_filter = eligible_cust_filter & (snapshot['CRM_ARR_BAND_BROAD'] == 'c) 100K+')
        metrics['Eligible customers ($100k+)'] = snapshot[eligible_cust_filter & (snapshot['CRM_ARR_BAND_BROAD'] == 'c) 100K+')]['CRM_ACCOUNT_ID'].nunique()

        # Eligible instances (Instance grain, 60+ day tenure)
        metrics['Eligible instances'] = snapshot[eligible_tenure_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Adoption rates
        if metrics['Eligible customers'] > 0:
            metrics['Customer adoption %'] = (metrics['Adopted customers'] / metrics['Eligible customers'] * 100)
        else:
            metrics['Customer adoption %'] = 0

        if metrics['Eligible customers ($100k+)'] > 0:
            metrics['Customer adoption % ($100k+)'] = (metrics['Adopted customers ($100k+)'] / metrics['Eligible customers ($100k+)'] * 100)
        else:
            metrics['Customer adoption % ($100k+)'] = 0

        if metrics['Eligible instances'] > 0:
            metrics['Instance adoption %'] = (metrics['Adopted instances'] / metrics['Eligible instances'] * 100)
        else:
            metrics['Instance adoption %'] = 0

        # AR Rates - only instances with > 0 ARs
        ar_filter = snapshot['AR_RATE_PAID'] > 0
        if ar_filter.sum() > 0:
            metrics['Median AR Rate'] = snapshot[ar_filter]['AR_RATE_PAID'].median()
        else:
            metrics['Median AR Rate'] = 0

        # AR Utilization Run Rate - Complex formula with multiple filters
        # Formula: SUM(AUTOMATED_RESOLUTIONS_USED_LAST_28D_NORMALIZED) / SUM(PRORATED_ALLOWANCE_LAST_28D)
        # Where: ARR>0, ALLOWANCE_PERIOD>=12, DAYS_INTO_CYCLE>28, TOTAL_ALLOWANCE<1M
        # Note: Data already filtered by product type
        ar_util_filter = (
            (snapshot['AUTOMATED_RESOLUTIONS_NET_ARR_USD'] > 0) &
            (snapshot['ALLOWANCE_PERIOD_MONTHS'] >= 12) &
            (snapshot['DAYS_INTO_ALLOWANCE_CYCLE'] > 28) &
            (snapshot['TOTAL_ALLOWANCE'] < 1000000)
        )
        if ar_util_filter.sum() > 0:
            numerator = snapshot[ar_util_filter]['AUTOMATED_RESOLUTIONS_USED_LAST_28D_NORMALIZED'].sum()
            denominator = snapshot[ar_util_filter]['PRORATED_ALLOWANCE_LAST_28D'].sum()
            if denominator > 0:
                metrics['AR Utilization Run Rate'] = numerator / denominator
            else:
                metrics['AR Utilization Run Rate'] = 0
        else:
            metrics['AR Utilization Run Rate'] = 0

        # Channel-specific AR rates (only instances with > 0 ARs)
        email_ar_filter = snapshot['EMAIL_AR_RATE_PAID'] > 0
        if email_ar_filter.sum() > 0:
            metrics['Median AR Rate - Email'] = snapshot[email_ar_filter]['EMAIL_AR_RATE_PAID'].median()
        else:
            metrics['Median AR Rate - Email'] = 0

        msg_ar_filter = snapshot['MSG_AR_RATE_PAID'] > 0
        if msg_ar_filter.sum() > 0:
            metrics['Median AR Rate - Messaging'] = snapshot[msg_ar_filter]['MSG_AR_RATE_PAID'].median()
        else:
            metrics['Median AR Rate - Messaging'] = 0

        # AR Rate buckets - Count unique instances (not rows)
        # Formula: AR>0, AR<0.3 (data already filtered by product type)
        ar_0_30_filter = (
            (snapshot['AR_RATE_PAID'] > 0) &
            (snapshot['AR_RATE_PAID'] < 0.3)
        )
        metrics['Instances AR 0-30%'] = snapshot[ar_0_30_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Formula: AR>=0.3 (data already filtered by product type)
        ar_30_plus_filter = (snapshot['AR_RATE_PAID'] >= 0.3)
        metrics['Instances AR 30%+'] = snapshot[ar_30_plus_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Total active instances with bot deployed
        # Data already filtered by product type
        if 'FIRST_BOT_DEPLOYED_DATE_PAID' in snapshot.columns:
            bot_deployed_filter = (
                (snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'].notna()) &
                (snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'] != '')
            )
            metrics['Total active instances with bot deployed'] = bot_deployed_filter.sum()
        else:
            metrics['Total active instances with bot deployed'] = 0

        # Bot deployed share % - # bot interactions / # tickets
        total_tickets = snapshot['TOTAL_CREATED_TICKETS_28D'].sum()
        total_bot_interactions = snapshot['BOT_INTERACTIONS_PAID'].sum()
        if total_tickets > 0:
            metrics['Bot deployed share %'] = total_bot_interactions / total_tickets
        else:
            metrics['Bot deployed share %'] = 0

        # Store numerator and denominator for debugging
        metrics['Bot deployed share - numerator'] = total_bot_interactions
        metrics['Bot deployed share - denominator'] = total_tickets

        # Automated Resolutions
        metrics['Total ARs (28d)'] = snapshot['AUTOMATED_RESOLUTIONS_PAID'].sum()

        # Bot interactions
        metrics['Total Bot Interactions (28d)'] = snapshot['BOT_INTERACTIONS_PAID'].sum()

        # Tickets
        metrics['Total Tickets (28d)'] = snapshot['TOTAL_CREATED_TICKETS_28D'].sum()

        # BSAT
        total_responses = snapshot['TOTAL_RESPONSES_28D'].sum()
        top_box = snapshot['TOP_BOX_5_STAR_28D'].sum()
        metrics['Top Box BSAT %'] = (top_box / total_responses * 100) if total_responses > 0 else 0
        metrics['# Responses'] = total_responses
        metrics['# Top Box Responses'] = top_box

        # Integrations - Formula: 60+ Day Tenure=TRUE, ACTIVE_INTEGRATIONS_28D > 0
        # Data already filtered by product type
        integration_filter = (
            eligible_tenure_filter &
            (snapshot['ACTIVE_INTEGRATIONS_28D'] > 0)
        )
        metrics['# instances with integrations'] = snapshot[integration_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Verified Resolution Rate Categories (CRM level)
        # Categories: Poor (<50%), Acceptable (50-80%), Optimal (>80%)
        if 'VERIFIED_AUTOMATED_RESOLUTION_RATE_PAID' in snapshot.columns:
            # Group by CRM account and calculate average verified rate across instances
            crm_verified_rates = snapshot.groupby('CRM_ACCOUNT_ID')['VERIFIED_AUTOMATED_RESOLUTION_RATE_PAID'].mean()

            # Count customers in each category
            metrics['Customers - Poor Verified (<50%)'] = (crm_verified_rates < 0.5).sum()
            metrics['Customers - Acceptable Verified (50-80%)'] = ((crm_verified_rates >= 0.5) & (crm_verified_rates <= 0.8)).sum()
            metrics['Customers - Optimal Verified (>80%)'] = (crm_verified_rates > 0.8).sum()
        else:
            metrics['Customers - Poor Verified (<50%)'] = 0
            metrics['Customers - Acceptable Verified (50-80%)'] = 0
            metrics['Customers - Optimal Verified (>80%)'] = 0

        # Gen3 classification (operates on already-filtered data)
        if 'GEN2_3_CLASSIFICATION' in snapshot.columns:
            metrics['Gen3 Instances'] = len(snapshot[snapshot['GEN2_3_CLASSIFICATION'] == 'Gen3'])
            metrics['Gen2 Instances'] = len(snapshot[snapshot['GEN2_3_CLASSIFICATION'] == 'Gen2'])
        else:
            metrics['Gen3 Instances'] = 0
            metrics['Gen2 Instances'] = 0

        # Go-live dates - Formula: date > previous_week AND date <= current_week
        # Data already filtered by product type
        one_week_ago = date - timedelta(days=7)

        if 'ACTUAL_GO_LIVE_DATE' in snapshot.columns:
            snapshot['ACTUAL_GO_LIVE_DATE'] = pd.to_datetime(snapshot['ACTUAL_GO_LIVE_DATE'], errors='coerce')
            actual_golive_filter = (
                (snapshot['ACTUAL_GO_LIVE_DATE'] > one_week_ago) &
                (snapshot['ACTUAL_GO_LIVE_DATE'] <= date)
            )
            metrics['Actual Go-Live (past week)'] = snapshot[actual_golive_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['Actual Go-Live (past week)'] = 0

        if 'PROJECTED_GO_LIVE_DATE' in snapshot.columns:
            snapshot['PROJECTED_GO_LIVE_DATE'] = pd.to_datetime(snapshot['PROJECTED_GO_LIVE_DATE'], errors='coerce')
            projected_golive_filter = (
                (snapshot['PROJECTED_GO_LIVE_DATE'] > one_week_ago) &
                (snapshot['PROJECTED_GO_LIVE_DATE'] <= date)
            )
            metrics['Projected Go-Live (next week)'] = snapshot[projected_golive_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['Projected Go-Live (next week)'] = 0

        # Bots deployed this week - Formula: FIRST_BOT_DEPLOYED_DATE > previous_week AND <= current_week
        # Data already filtered by product type
        if 'FIRST_BOT_DEPLOYED_DATE_PAID' in snapshot.columns:
            snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'] = pd.to_datetime(snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'], errors='coerce')
            bots_deployed_filter = (
                (snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'] > one_week_ago) &
                (snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'] <= date)
            )
            metrics['Bots deployed this week'] = snapshot[bots_deployed_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['Bots deployed this week'] = 0

        metrics_list.append(metrics)

    return pd.DataFrame(metrics_list)

def calculate_cohort_metrics(df):
    """
    Calculate customer counts by cohort for each snapshot date
    Returns a dataframe with Date, Cohort, and # Customers
    """
    # Ensure date column is datetime
    df['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(df['SOURCE_SNAPSHOT_DATE'])

    # Sort by date
    df = df.sort_values('SOURCE_SNAPSHOT_DATE')

    # Get unique dates
    dates = df['SOURCE_SNAPSHOT_DATE'].unique()

    cohort_metrics_list = []

    for date in dates:
        snapshot = df[df['SOURCE_SNAPSHOT_DATE'] == date].copy()

        # Data is already filtered by product type at this point
        # No need to re-filter, just use the snapshot as-is
        snapshot_penetrated = snapshot

        # Check if COHORT column exists
        if 'COHORT' not in snapshot_penetrated.columns:
            continue

        # Group by cohort and count unique CRM accounts
        cohort_counts = snapshot_penetrated.groupby('COHORT')['CRM_ACCOUNT_ID'].nunique().reset_index()
        cohort_counts.columns = ['Cohort', '# Customers']
        cohort_counts['Date'] = date

        cohort_metrics_list.append(cohort_counts)

    if len(cohort_metrics_list) == 0:
        return pd.DataFrame(columns=['Date', 'Cohort', '# Customers'])

    # Combine all snapshots
    cohort_df = pd.concat(cohort_metrics_list, ignore_index=True)
    cohort_df = cohort_df[['Date', 'Cohort', '# Customers']]

    return cohort_df

# Sidebar for file upload
with st.sidebar:
    # Auto-load on first visit
    if st.session_state.global_data is None:
        with st.spinner("Loading data from Snowflake..."):
            df, error = load_data_from_snowflake()

            if error:
                st.error(f"Error loading data: {error}")
                st.markdown("**Troubleshooting:**")
                st.markdown("- Ensure you're running this in Snowflake Streamlit")
                st.markdown("- Check table permissions")
            elif df is not None:
                st.session_state.global_data = df
                st.session_state.last_load_time = datetime.now()

    if st.session_state.global_data is not None and len(st.session_state.global_data) > 0:
        st.divider()
        st.header("Filters")

        st.info("**Note:** Apply filters below to segment your data. Default shows all instances.")

        gdf = st.session_state.global_data.copy()
        gdf['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf['SOURCE_SNAPSHOT_DATE'])

        # Date filter - Simple date selector defaulting to latest snapshot
        if 'SOURCE_SNAPSHOT_DATE' in gdf.columns and not gdf['SOURCE_SNAPSHOT_DATE'].isna().all():
            try:
                # Get available snapshot dates (sorted newest to oldest)
                available_dates = sorted(gdf['SOURCE_SNAPSHOT_DATE'].dt.date.unique(), reverse=True)

                # Simple date selector with latest as default (index 0)
                selected_date = st.selectbox(
                    "As of Date",
                    options=available_dates,
                    index=0,  # Default to latest (first in list)
                    key="as_of_date",
                    help="Select a snapshot date to view metrics. Defaults to the latest available date."
                )

            except Exception as e:
                st.error(f"Error with date filter: {e}")
                selected_date = None

        # Region filter
        if 'CRM_REGION' in gdf.columns:
            region_values = gdf['CRM_REGION'].dropna().unique()
            regions = ['All'] + sorted([str(r) for r in region_values if r is not None])
            selected_region = st.selectbox("Region", regions, key="region_filter")
        else:
            selected_region = 'All'

        # ARR Band filter
        if 'CRM_ARR_BAND_BROAD' in gdf.columns:
            arr_values = gdf['CRM_ARR_BAND_BROAD'].dropna().unique()
            arr_bands = ['All'] + sorted([str(a) for a in arr_values if a is not None])
            selected_arr_band = st.selectbox("ARR Band", arr_bands, key="arr_filter")
        else:
            selected_arr_band = 'All'

        # Responsibility filter
        if 'RESPONSIBILITY' in gdf.columns:
            resp_values = gdf['RESPONSIBILITY'].dropna().unique()
            responsibilities = ['All'] + sorted([str(r) for r in resp_values if r is not None])
            selected_responsibility = st.selectbox("Responsibility", responsibilities, key="resp_filter")
        else:
            selected_responsibility = 'All'

        # Segment filter
        if 'CRM_MARKET_SEGMENT' in gdf.columns:
            segment_values = gdf['CRM_MARKET_SEGMENT'].dropna().unique()
            segments = ['All'] + sorted([str(s) for s in segment_values if s is not None])
            selected_segment = st.selectbox("Segment", segments, key="segment_filter")
        else:
            selected_segment = 'All'

        # Sub-Region filter
        if 'CRM_SUB_REGION' in gdf.columns:
            subregion_values = gdf['CRM_SUB_REGION'].dropna().unique()
            subregions = ['All'] + sorted([str(s) for s in subregion_values if s is not None])
            selected_subregion = st.selectbox("Sub-Region", subregions, key="subregion_filter")
        else:
            selected_subregion = 'All'

        # Industry filter
        if 'CRM_INDUSTRY' in gdf.columns:
            industry_values = gdf['CRM_INDUSTRY'].dropna().unique()
            industries = ['All'] + sorted([str(i) for i in industry_values if i is not None])
            selected_industry = st.selectbox("Industry", industries, key="industry_filter")
        else:
            selected_industry = 'All'

        # AI Agents Product filter (multi-select)
        st.markdown("**AI Agents Product**")
        ai_product_options = st.multiselect(
            "Select product type(s)",
            options=['Advanced', 'Essentials'],
            default=['Advanced', 'Essentials'],
            label_visibility="collapsed",
            help="**Advanced**: INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED = TRUE (includes instances with both Advanced and Essentials)\n\n**Essentials**: Instances with PAID penetrated but NOT Advanced penetrated (Essentials-only instances)",
            key="product_filter"
        )

# Main content
if st.session_state.global_data is None:
    # Welcome screen
    st.markdown("""
    ## Welcome to AIAA Command Central Dashboard 🎯

    This dashboard automatically calculates all scorecard metrics from live Snowflake data, **matching the Excel formulas exactly**.

    ### Data Source:
    - **Table**: `presentation.success.ai_agents_advanced_command_central`
    - **Updates**: Live data refreshed automatically

    ### Key Filters (Automatic):
    - **ARR Band**: Filtered to "b) 12K-100K, c) 100K+" (matching Excel)
    - **Tenure**: Uses '60+ Day Tenure?' column for eligible calculations
    - **AR Rates**: Only includes instances with > 0 ARs

    ### Metrics Calculated:
    - **Impact Metrics**: Customer/instance counts, adoption rates
    - **AR Utilization**: AR rates by channel, utilization run rate
    - **Bot Deployment**: Bot deployment stats, Gen2/Gen3 classification
    - **BSAT**: Top box satisfaction scores
    - **Go-Live**: Actual and projected go-live tracking

    ### Getting Started:
    1. Click **"Load Data from Snowflake"** in the sidebar (or wait for auto-load)
    2. Metrics calculate automatically for each week
    3. Numbers will match your Excel scorecard exactly
    4. Use additional filters (date, region) to drill down

    **Click "Load Data from Snowflake" in the sidebar to begin!**
    """)

else:
    gdf = st.session_state.global_data.copy()
    gdf['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf['SOURCE_SNAPSHOT_DATE'])

    # Store date filter info before applying filters
    display_selected_date = None
    if 'selected_date' in locals() and selected_date is not None:
        display_selected_date = selected_date

    # Apply NON-DATE filters first (keep all dates for comparison calculations)
    if 'selected_region' in locals() and selected_region != 'All':
        gdf = gdf[gdf['CRM_REGION'] == selected_region]

    if 'selected_arr_band' in locals() and selected_arr_band != 'All':
        gdf = gdf[gdf['CRM_ARR_BAND_BROAD'] == selected_arr_band]

    if 'selected_responsibility' in locals() and selected_responsibility != 'All':
        gdf = gdf[gdf['RESPONSIBILITY'] == selected_responsibility]

    if 'selected_segment' in locals() and selected_segment != 'All':
        gdf = gdf[gdf['CRM_MARKET_SEGMENT'] == selected_segment]

    if 'selected_subregion' in locals() and selected_subregion != 'All':
        gdf = gdf[gdf['CRM_SUB_REGION'] == selected_subregion]

    if 'selected_industry' in locals() and selected_industry != 'All':
        gdf = gdf[gdf['CRM_INDUSTRY'] == selected_industry]

    # AI Agents Product filter (multi-select with hierarchy)
    if 'ai_product_options' in locals() and len(ai_product_options) > 0:
        # Build filter based on selected options with hierarchy:
        # - Advanced: INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED = TRUE
        # - Essentials: Advanced = FALSE AND INSTANCE_IS_AI_AGENTS_PAID_PENETRATED = TRUE
        product_filter = pd.Series([False] * len(gdf), index=gdf.index)

        if 'Advanced' in ai_product_options:
            # Advanced takes precedence
            product_filter |= (gdf['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True)

        if 'Essentials' in ai_product_options:
            # Essentials only if Advanced is False
            essentials_filter = (
                (gdf['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == False) &
                (gdf['INSTANCE_IS_AI_AGENTS_PAID_PENETRATED'] == True)
            )
            product_filter |= essentials_filter

        gdf = gdf[product_filter]

    # Calculate scorecard metrics for ALL dates (needed for comparisons)
    with st.spinner("Calculating scorecard metrics..."):
        try:
            scorecard_df = calculate_scorecard_metrics(gdf)
        except Exception as e:
            st.error(f"Error calculating metrics: {e}")
            st.exception(e)
            st.stop()

    # Now apply date filter to the RAW data for tabs that need it (Data Explorer)
    gdf_filtered = gdf.copy()
    if display_selected_date is not None:
        # Filter to the selected date
        gdf_filtered = gdf_filtered[gdf_filtered['SOURCE_SNAPSHOT_DATE'] == pd.to_datetime(display_selected_date)]

    # Create tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        ":material/query_stats: Scorecard",
        ":material/trending_up: Trends",
        ":material/groups: Cohort Analysis",
        ":material/warning: Adoption Loss",
        ":material/table: Data Explorer",
        ":material/info: Metrics Guide"
    ])

    with tab1:
        st.subheader("AIAA Command Central Scorecard")

        if len(scorecard_df) == 0:
            st.warning("No data available for the selected filters.")
        else:
            # Determine which date to display based on filter mode
            if display_selected_date is not None:
                # Show only the selected date
                scorecard_display_df = scorecard_df[scorecard_df['Date'] == pd.to_datetime(display_selected_date)]
                if len(scorecard_display_df) == 0:
                    st.warning(f"No data available for {display_selected_date}")
                    st.stop()
                latest = scorecard_display_df.iloc[-1]
            else:
                # Show latest from the range (or all dates)
                latest = scorecard_df.iloc[-1]

            latest_date = latest['Date'].strftime('%Y-%m-%d') if isinstance(latest['Date'], pd.Timestamp) else str(latest['Date'])

            if display_selected_date is not None:
                st.markdown(f"**Showing data as of:** {latest_date}")
            else:
                st.markdown(f"**Latest Snapshot:** {latest_date} | **Total Snapshots:** {len(scorecard_df)}")

            # Helper function to calculate changes using exact date matching
            def calculate_changes(metric_name, format_type='number'):
                current = latest[metric_name]
                current_date = pd.to_datetime(latest['Date'])

                # Ensure scorecard_df has Date as datetime
                scorecard_df_dated = scorecard_df.copy()
                scorecard_df_dated['Date'] = pd.to_datetime(scorecard_df_dated['Date'])

                # Determine if we should use percentage point change (for percentages) or absolute change (for counts)
                is_percentage_metric = format_type in ['percent', 'percent_decimal']

                # WoW change (exact match for 7 days ago)
                wow_change = None
                one_week_ago = current_date - pd.Timedelta(days=7)
                prev_week_data = scorecard_df_dated[scorecard_df_dated['Date'] == one_week_ago]
                if len(prev_week_data) > 0:
                    prev_week = prev_week_data.iloc[0][metric_name]
                    if not pd.isna(prev_week):
                        if is_percentage_metric:
                            # Percentage point change (for % metrics)
                            wow_change = (current - prev_week) * 100
                        else:
                            # Absolute change (for count metrics)
                            wow_change = current - prev_week

                # 4-week change (exact match for 28 days ago)
                four_week_change = None
                four_weeks_ago = current_date - pd.Timedelta(days=28)
                four_week_data = scorecard_df_dated[scorecard_df_dated['Date'] == four_weeks_ago]
                if len(four_week_data) > 0:
                    four_week_val = four_week_data.iloc[0][metric_name]
                    if not pd.isna(four_week_val):
                        if is_percentage_metric:
                            # Percentage point change (for % metrics)
                            four_week_change = (current - four_week_val) * 100
                        else:
                            # Absolute change (for count metrics)
                            four_week_change = current - four_week_val

                # QTD change (quarter-to-date)
                qtd_change = None
                quarter_start = pd.Timestamp(current_date.year, ((current_date.quarter - 1) * 3) + 1, 1)
                qtd_data = scorecard_df_dated[scorecard_df_dated['Date'] >= quarter_start].sort_values('Date')
                if len(qtd_data) >= 2:
                    qtd_first = qtd_data.iloc[0][metric_name]
                    if not pd.isna(qtd_first):
                        if is_percentage_metric:
                            # Percentage point change (for % metrics)
                            qtd_change = (current - qtd_first) * 100
                        else:
                            # Absolute change (for count metrics)
                            qtd_change = current - qtd_first

                # Format current value
                if format_type == 'percent':
                    current_str = f"{current:.1%}"
                elif format_type == 'percent_decimal':
                    current_str = f"{current:.1f}%"
                else:
                    current_str = f"{int(current):,}"

                # Format changes
                if is_percentage_metric:
                    # Percentage point change (pp)
                    wow_str = f"{wow_change:+.1f}pp" if wow_change is not None else "—"
                    four_week_str = f"{four_week_change:+.1f}pp" if four_week_change is not None else "—"
                    qtd_str = f"{qtd_change:+.1f}pp" if qtd_change is not None else "—"
                else:
                    # Absolute change (no suffix, just the number with sign)
                    wow_str = f"{wow_change:+,.0f}" if wow_change is not None else "—"
                    four_week_str = f"{four_week_change:+,.0f}" if four_week_change is not None else "—"
                    qtd_str = f"{qtd_change:+,.0f}" if qtd_change is not None else "—"

                return current_str, wow_str, four_week_str, qtd_str

            # Define metrics by category
            metrics_config = {
                "📊 Business Metrics": [
                    ("AR Utilization Run Rate", "AR Utilization Run Rate", "percent"),
                ],
                "🎯 Impact Metrics": [
                    ("# Customers", "# customers", "number"),
                    ("# Instances", "# instances", "number"),
                    ("Adopted Customers", "Adopted customers", "number"),
                    ("Adopted Instances", "Adopted instances", "number"),
                    ("Adopted Customers ($100k+)", "Adopted customers ($100k+)", "number"),
                    ("Eligible Customers", "Eligible customers", "number"),
                    ("Eligible Customers ($100k+)", "Eligible customers ($100k+)", "number"),
                    ("Eligible Instances", "Eligible instances", "number"),
                ],
                "📈 Adoption Rates": [
                    ("Customer Adoption %", "Customer adoption %", "percent_decimal"),
                    ("Customer Adoption % ($100k+)", "Customer adoption % ($100k+)", "percent_decimal"),
                    ("Instance Adoption %", "Instance adoption %", "percent_decimal"),
                ],
                "🤖 AR Rates & Control": [
                    ("Median AR Rate", "Median AR Rate", "percent"),
                    ("Median AR Rate - Email", "Median AR Rate - Email", "percent"),
                    ("Median AR Rate - Messaging", "Median AR Rate - Messaging", "percent"),
                    ("# Instances with Integrations", "# instances with integrations", "number"),
                    ("Instances AR 0-30%", "Instances AR 0-30%", "number"),
                    ("Instances AR 30%+", "Instances AR 30%+", "number"),
                    ("Total ARs (28d)", "Total ARs (28d)", "number"),
                ],
                "✅ Verified Resolution Quality": [
                    ("Poor (<50% Verified)", "Customers - Poor Verified (<50%)", "number"),
                    ("Acceptable (50-80% Verified)", "Customers - Acceptable Verified (50-80%)", "number"),
                    ("Optimal (>80% Verified)", "Customers - Optimal Verified (>80%)", "number"),
                ],
                "🚀 Bot Deployment": [
                    ("Total Active Instances with Bot", "Total active instances with bot deployed", "number"),
                    ("Bots Deployed This Week", "Bots deployed this week", "number"),
                    ("Bot Deployed Share %", "Bot deployed share %", "percent"),
                    ("Gen3 Instances", "Gen3 Instances", "number"),
                    ("Actual Go-Live (Past Week)", "Actual Go-Live (past week)", "number"),
                    ("Projected Go-Live (Next Week)", "Projected Go-Live (next week)", "number"),
                ],
                "⭐ Customer Satisfaction": [
                    ("Top Box BSAT %", "Top Box BSAT %", "percent_decimal"),
                    ("# Top Box Responses", "# Top Box Responses", "number"),
                    ("# Responses", "# Responses", "number"),
                ],
            }

            # Display metrics as tables by category
            for category, metrics in metrics_config.items():
                st.markdown(f"### {category}")

                # Build table data
                table_data = []
                for display_name, metric_key, format_type in metrics:
                    current, wow, four_week, qtd = calculate_changes(metric_key, format_type)
                    table_data.append({
                        "Metric": display_name,
                        "Current Value": current,
                        "WoW Change": wow,
                        "4-Week Change": four_week,
                        "QTD Change": qtd
                    })

                # Display as dataframe with Metric as index to hide row numbers
                metrics_table = pd.DataFrame(table_data)
                metrics_table = metrics_table.set_index('Metric')
                st.dataframe(metrics_table, use_container_width=True, height=min(len(table_data) * 35 + 38, 400))

            st.divider()

            # Download
            st.download_button(
                label=":material/download: Download Full Scorecard (CSV)",
                data=scorecard_df.to_csv(index=False).encode('utf-8'),
                file_name=f"aiaa_scorecard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )

    with tab2:
        st.subheader("Trend Analysis")

        if len(scorecard_df) > 0:
            st.line_chart(scorecard_df, x='Date', y=['# customers', 'Adopted customers'], height=300)
            st.divider()
            st.line_chart(scorecard_df, x='Date', y=['# instances', 'Adopted instances'], height=300)

    with tab3:
        st.subheader("Cohort Analysis")

        # Calculate cohort metrics (use full date range for calculations)
        with st.spinner("Calculating cohort metrics..."):
            try:
                cohort_df = calculate_cohort_metrics(gdf)
            except Exception as e:
                st.error(f"Error calculating cohort metrics: {e}")
                st.exception(e)
                st.stop()

        if len(cohort_df) == 0:
            st.warning("No cohort data available. Please ensure the 'COHORT' column exists in your data.")
        else:
            # Get latest date
            latest_date = cohort_df['Date'].max()
            latest_cohorts = cohort_df[cohort_df['Date'] == latest_date].copy()

            st.markdown(f"**Latest Snapshot:** {latest_date.strftime('%Y-%m-%d') if isinstance(latest_date, pd.Timestamp) else str(latest_date)}")

            # Helper function to calculate cohort changes
            def calculate_cohort_changes(cohort_name):
                cohort_data = cohort_df[cohort_df['Cohort'] == cohort_name].copy()
                cohort_data['Date'] = pd.to_datetime(cohort_data['Date'])
                cohort_data = cohort_data.sort_values('Date')

                if len(cohort_data) == 0:
                    return None, None, None, None

                # Get current value
                current_row = cohort_data[cohort_data['Date'] == latest_date]
                if len(current_row) == 0:
                    return None, None, None, None

                current = current_row.iloc[0]['# Customers']

                # WoW change (exact match for 7 days ago)
                wow_change = None
                one_week_ago = latest_date - pd.Timedelta(days=7)
                prev_week_data = cohort_data[cohort_data['Date'] == one_week_ago]
                if len(prev_week_data) > 0:
                    prev_week = prev_week_data.iloc[0]['# Customers']
                    if not pd.isna(prev_week):
                        wow_change = current - prev_week

                # 4-week change (exact match for 28 days ago)
                four_week_change = None
                four_weeks_ago = latest_date - pd.Timedelta(days=28)
                four_week_data = cohort_data[cohort_data['Date'] == four_weeks_ago]
                if len(four_week_data) > 0:
                    four_week_val = four_week_data.iloc[0]['# Customers']
                    if not pd.isna(four_week_val):
                        four_week_change = current - four_week_val

                # QTD change (quarter-to-date)
                qtd_change = None
                quarter_start = pd.Timestamp(latest_date.year, ((latest_date.quarter - 1) * 3) + 1, 1)
                qtd_data = cohort_data[cohort_data['Date'] >= quarter_start].sort_values('Date')
                if len(qtd_data) >= 2:
                    qtd_first = qtd_data.iloc[0]['# Customers']
                    if not pd.isna(qtd_first):
                        qtd_change = current - qtd_first

                # Format values
                current_str = f"{int(current):,}"
                wow_str = f"{wow_change:+,.0f}" if wow_change is not None else "—"
                four_week_str = f"{four_week_change:+,.0f}" if four_week_change is not None else "—"
                qtd_str = f"{qtd_change:+,.0f}" if qtd_change is not None else "—"

                return current_str, wow_str, four_week_str, qtd_str

            # Build cohort table
            st.markdown("### 📊 Customer Counts by Cohort")

            table_data = []
            for cohort in sorted(latest_cohorts['Cohort'].unique()):
                current, wow, four_week, qtd = calculate_cohort_changes(cohort)
                if current is not None:
                    table_data.append({
                        "Cohort": cohort,
                        "Current # Customers": current,
                        "WoW Change": wow,
                        "4-Week Change": four_week,
                        "QTD Change": qtd
                    })

            if len(table_data) > 0:
                cohort_table = pd.DataFrame(table_data)
                cohort_table = cohort_table.set_index('Cohort')
                st.dataframe(cohort_table, use_container_width=True, height=min(len(table_data) * 35 + 38, 600))
            else:
                st.warning("No cohort data available for the latest snapshot.")

            st.divider()

            # Time series table
            st.markdown("### 📅 Cohort Trends Over Time")

            # Pivot the data for better visualization
            cohort_pivot = cohort_df.pivot(index='Date', columns='Cohort', values='# Customers')
            cohort_pivot = cohort_pivot.sort_index(ascending=False)
            cohort_pivot.index = pd.to_datetime(cohort_pivot.index).strftime('%Y-%m-%d')

            st.dataframe(cohort_pivot, use_container_width=True, height=400)

            # Download
            st.download_button(
                label=":material/download: Download Cohort Data (CSV)",
                data=cohort_df.to_csv(index=False).encode('utf-8'),
                file_name=f"aiaa_cohort_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )

    with tab4:
        st.subheader("Adoption Loss Analysis")

        if len(gdf) == 0:
            st.warning("No data available for the selected filters.")
        else:
            # Get the two most recent snapshots
            gdf_sorted = gdf.copy()
            gdf_sorted['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf_sorted['SOURCE_SNAPSHOT_DATE'])
            gdf_sorted = gdf_sorted.sort_values('SOURCE_SNAPSHOT_DATE')

            available_dates = gdf_sorted['SOURCE_SNAPSHOT_DATE'].unique()

            if len(available_dates) < 2:
                st.warning("Need at least 2 snapshots to compare adoption changes. Currently only have 1 snapshot.")
            else:
                latest_date = available_dates[-1]
                previous_date = available_dates[-2]

                st.markdown(f"**Comparing:** {previous_date.strftime('%Y-%m-%d')} → {latest_date.strftime('%Y-%m-%d')}")

                # Get snapshots
                latest_snapshot = gdf_sorted[gdf_sorted['SOURCE_SNAPSHOT_DATE'] == latest_date].copy()
                previous_snapshot = gdf_sorted[gdf_sorted['SOURCE_SNAPSHOT_DATE'] == previous_date].copy()

                # Define adopted filter (matching the main scorecard logic)
                eligible_tenure_filter_prev = previous_snapshot['60+ Day Tenure?'] == True
                eligible_tenure_filter_latest = latest_snapshot['60+ Day Tenure?'] == True

                # Previous adopted customers
                crm_adopted_filter_prev = (
                    ((previous_snapshot['CRM_IS_AI_AGENTS_ADVANCED_ADOPTED'] == True) & (previous_snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True)) |
                    ((previous_snapshot['CRM_IS_AI_AGENTS_PAID_ADOPTED'] == True) & (previous_snapshot['CRM_IS_AI_AGENTS_PAID_PENETRATED'] == True) & (previous_snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == False))
                ) & eligible_tenure_filter_prev

                # Latest adopted customers
                crm_adopted_filter_latest = (
                    ((latest_snapshot['CRM_IS_AI_AGENTS_ADVANCED_ADOPTED'] == True) & (latest_snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True)) |
                    ((latest_snapshot['CRM_IS_AI_AGENTS_PAID_ADOPTED'] == True) & (latest_snapshot['CRM_IS_AI_AGENTS_PAID_PENETRATED'] == True) & (latest_snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == False))
                ) & eligible_tenure_filter_latest

                # Get sets of adopted CRM account IDs
                prev_adopted_ids = set(previous_snapshot[crm_adopted_filter_prev]['CRM_ACCOUNT_ID'].unique())
                latest_adopted_ids = set(latest_snapshot[crm_adopted_filter_latest]['CRM_ACCOUNT_ID'].unique())

                # Find customers who lost adoption
                lost_adoption_ids = prev_adopted_ids - latest_adopted_ids

                # Find customers who gained adoption
                gained_adoption_ids = latest_adopted_ids - prev_adopted_ids

                # Display metrics
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Lost Adoption", len(lost_adoption_ids), delta=f"-{len(lost_adoption_ids)}", delta_color="inverse")
                with col2:
                    st.metric("Gained Adoption", len(gained_adoption_ids), delta=f"+{len(gained_adoption_ids)}")
                with col3:
                    net_change = len(gained_adoption_ids) - len(lost_adoption_ids)
                    st.metric("Net Change", net_change, delta=f"{net_change:+d}")

                st.divider()

                # Show customers who lost adoption
                if len(lost_adoption_ids) > 0:
                    st.markdown("### 🔻 Customers Who Lost Adoption")

                    # Get details from both snapshots for these customers
                    lost_customers_latest = latest_snapshot[latest_snapshot['CRM_ACCOUNT_ID'].isin(lost_adoption_ids)].copy()
                    lost_customers_prev = previous_snapshot[previous_snapshot['CRM_ACCOUNT_ID'].isin(lost_adoption_ids)].copy()

                    # Aggregate metrics by CRM account for both snapshots
                    # Latest snapshot
                    lost_latest_agg = lost_customers_latest.groupby('CRM_ACCOUNT_ID').agg({
                        'CRM_ACCOUNT_NAME': 'first',
                        'CRM_REGION': 'first',
                        'CRM_ARR_BAND_BROAD': 'first',
                        'CRM_MARKET_SEGMENT': 'first',
                        'AR_RATE_PAID': 'mean',  # Average AR rate across instances
                        'AUTOMATED_RESOLUTIONS_PAID': 'sum'  # Sum ARs across instances
                    }).reset_index()

                    # Previous snapshot
                    lost_prev_agg = lost_customers_prev.groupby('CRM_ACCOUNT_ID').agg({
                        'AR_RATE_PAID': 'mean',
                        'AUTOMATED_RESOLUTIONS_PAID': 'sum'
                    }).reset_index()

                    # Merge current and previous data
                    lost_df = lost_latest_agg.merge(
                        lost_prev_agg,
                        on='CRM_ACCOUNT_ID',
                        how='left',
                        suffixes=('_Current', '_Previous')
                    )

                    # Rename columns for clarity
                    lost_df = lost_df.rename(columns={
                        'AR_RATE_PAID_Current': 'Current AR Rate',
                        'AR_RATE_PAID_Previous': 'Previous AR Rate',
                        'AUTOMATED_RESOLUTIONS_PAID_Current': 'Current ARs (28d)',
                        'AUTOMATED_RESOLUTIONS_PAID_Previous': 'Previous ARs (28d)'
                    })

                    # Initialize notes table
                    init_notes_table()

                    # Load existing notes for this snapshot
                    existing_notes = load_notes(latest_date)
                    notes_dict = dict(zip(existing_notes['CRM_ACCOUNT_ID'], existing_notes['NOTES'])) if len(existing_notes) > 0 else {}

                    # Debug: Show loaded notes
                    with st.expander("🔍 Debug: Loaded Notes", expanded=False):
                        st.write(f"**Snapshot Date:** {latest_date}")
                        st.write(f"**Number of notes loaded:** {len(existing_notes)}")
                        if len(existing_notes) > 0:
                            st.dataframe(existing_notes, use_container_width=True)
                        else:
                            st.info("No notes found for this snapshot date.")

                    # Merge notes into the dataframe
                    lost_df['Notes'] = lost_df['CRM_ACCOUNT_ID'].map(notes_dict).fillna('')

                    # Reorder columns
                    column_order = [
                        'CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME', 'CRM_REGION', 'CRM_ARR_BAND_BROAD', 'CRM_MARKET_SEGMENT',
                        'Current AR Rate', 'Previous AR Rate',
                        'Current ARs (28d)', 'Previous ARs (28d)', 'Notes'
                    ]
                    lost_df = lost_df[column_order].sort_values('CRM_ACCOUNT_NAME')

                    # Create display dataframe with formatted values
                    lost_df_display = lost_df.copy()
                    lost_df_display['Current AR Rate'] = lost_df_display['Current AR Rate'].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
                    lost_df_display['Previous AR Rate'] = lost_df_display['Previous AR Rate'].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
                    lost_df_display['Current ARs (28d)'] = lost_df_display['Current ARs (28d)'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "0")
                    lost_df_display['Previous ARs (28d)'] = lost_df_display['Previous ARs (28d)'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "0")

                    # Remove CRM_ACCOUNT_ID from display
                    lost_df_display_no_id = lost_df_display.drop(columns=['CRM_ACCOUNT_ID'])
                    st.dataframe(lost_df_display_no_id, use_container_width=True, height=400)

                    # Add notes input section
                    st.markdown("#### 📝 Add/Edit Notes")
                    st.info("Select a customer and add notes to explain why they are unadopted.")

                    # Get current user
                    try:
                        session = get_active_session()
                        current_user = session.sql("SELECT CURRENT_USER()").collect()[0][0]
                    except:
                        current_user = "unknown_user"

                    # Customer selector
                    customer_names = lost_df[['CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME']].values.tolist()
                    customer_display = [f"{name}" for id, name in customer_names]
                    customer_map = {f"{name}": id for id, name in customer_names}

                    selected_customer = st.selectbox("Select Customer", customer_display, key="lost_customer_select")

                    if selected_customer:
                        selected_id = customer_map[selected_customer]
                        existing_note = notes_dict.get(selected_id, "")

                        # Show debug info
                        with st.expander("🔍 Debug: Customer Info", expanded=False):
                            st.write(f"**Selected Customer:** {selected_customer}")
                            st.write(f"**CRM Account ID:** {selected_id}")
                            st.write(f"**Snapshot Date:** {latest_date}")
                            st.write(f"**Existing Note:** {existing_note if existing_note else '(none)'}")

                        note_text = st.text_area(
                            "Notes",
                            value=existing_note,
                            height=100,
                            placeholder="Enter notes explaining why this customer is unadopted...",
                            key=f"note_input_{selected_id}"
                        )

                        if st.button("💾 Save Note", key=f"save_note_{selected_id}"):
                            # Debug: show what we're about to save
                            st.write("**Debug - About to save:**")
                            st.write(f"- CRM Account ID: `{selected_id}`")
                            st.write(f"- Customer Name: `{selected_customer}`")
                            st.write(f"- Snapshot Date: `{latest_date}`")
                            st.write(f"- Note Text: `{note_text}`")
                            st.write(f"- Note Length: {len(note_text)} characters")
                            st.write(f"- User: `{current_user}`")

                            with st.spinner("Saving note..."):
                                success = save_note(selected_id, selected_customer, latest_date, note_text, current_user)
                            if success:
                                st.success(f"✓ Note saved for {selected_customer} (ID: {selected_id})")
                                st.info("Refreshing page to load updated notes...")
                                st.rerun()
                            else:
                                st.error("Failed to save note - check error message above")

                    # Download button (with unformatted data for Excel, excluding CRM_ACCOUNT_ID)
                    st.divider()
                    lost_df_download = lost_df.drop(columns=['CRM_ACCOUNT_ID'])
                    st.download_button(
                        label=":material/download: Download Lost Adoption List with Notes (CSV)",
                        data=lost_df_download.to_csv(index=False).encode('utf-8'),
                        file_name=f"lost_adoption_{previous_date.strftime('%Y%m%d')}_to_{latest_date.strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ No customers lost adoption between these snapshots!")

                st.divider()

                # Show customers who gained adoption
                if len(gained_adoption_ids) > 0:
                    st.markdown("### 🔺 Customers Who Gained Adoption")

                    # Get details from both snapshots for these customers
                    gained_customers_latest = latest_snapshot[latest_snapshot['CRM_ACCOUNT_ID'].isin(gained_adoption_ids)].copy()
                    gained_customers_prev = previous_snapshot[previous_snapshot['CRM_ACCOUNT_ID'].isin(gained_adoption_ids)].copy()

                    # Aggregate metrics by CRM account for both snapshots
                    # Latest snapshot
                    gained_latest_agg = gained_customers_latest.groupby('CRM_ACCOUNT_ID').agg({
                        'CRM_ACCOUNT_NAME': 'first',
                        'CRM_REGION': 'first',
                        'CRM_ARR_BAND_BROAD': 'first',
                        'CRM_MARKET_SEGMENT': 'first',
                        'AR_RATE_PAID': 'mean',  # Average AR rate across instances
                        'AUTOMATED_RESOLUTIONS_PAID': 'sum'  # Sum ARs across instances
                    }).reset_index()

                    # Previous snapshot (may not exist if customer was just added)
                    gained_prev_agg = gained_customers_prev.groupby('CRM_ACCOUNT_ID').agg({
                        'AR_RATE_PAID': 'mean',
                        'AUTOMATED_RESOLUTIONS_PAID': 'sum'
                    }).reset_index()

                    # Merge current and previous data
                    gained_df = gained_latest_agg.merge(
                        gained_prev_agg,
                        on='CRM_ACCOUNT_ID',
                        how='left',
                        suffixes=('_Current', '_Previous')
                    )

                    # Rename columns for clarity
                    gained_df = gained_df.rename(columns={
                        'AR_RATE_PAID_Current': 'Current AR Rate',
                        'AR_RATE_PAID_Previous': 'Previous AR Rate',
                        'AUTOMATED_RESOLUTIONS_PAID_Current': 'Current ARs (28d)',
                        'AUTOMATED_RESOLUTIONS_PAID_Previous': 'Previous ARs (28d)'
                    })

                    # Reorder columns
                    column_order = [
                        'CRM_ACCOUNT_NAME', 'CRM_REGION', 'CRM_ARR_BAND_BROAD', 'CRM_MARKET_SEGMENT',
                        'Current AR Rate', 'Previous AR Rate',
                        'Current ARs (28d)', 'Previous ARs (28d)'
                    ]
                    gained_df = gained_df[column_order].sort_values('CRM_ACCOUNT_NAME')

                    # Create display dataframe with formatted values
                    gained_df_display = gained_df.copy()
                    gained_df_display['Current AR Rate'] = gained_df_display['Current AR Rate'].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
                    gained_df_display['Previous AR Rate'] = gained_df_display['Previous AR Rate'].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
                    gained_df_display['Current ARs (28d)'] = gained_df_display['Current ARs (28d)'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "0")
                    gained_df_display['Previous ARs (28d)'] = gained_df_display['Previous ARs (28d)'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "0")

                    st.dataframe(gained_df_display, use_container_width=True, height=400)

                    # Download button (with unformatted data for Excel)
                    st.download_button(
                        label=":material/download: Download Gained Adoption List (CSV)",
                        data=gained_df.to_csv(index=False).encode('utf-8'),
                        file_name=f"gained_adoption_{previous_date.strftime('%Y%m%d')}_to_{latest_date.strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )

    with tab5:
        st.subheader("Data Explorer")

        all_columns = gdf_filtered.columns.tolist()
        default_cols = ['SOURCE_SNAPSHOT_DATE', 'INSTANCE_ACCOUNT_SUBDOMAIN', 'CRM_ACCOUNT_NAME', 'CRM_REGION', 'CRM_ARR_BAND_BROAD', 'AR_RATE_PAID']
        default_cols = [col for col in default_cols if col in all_columns]

        selected_columns = st.multiselect("Select columns", all_columns, default=default_cols if default_cols else all_columns[:10])

        if selected_columns:
            st.dataframe(gdf_filtered[selected_columns].head(1000), use_container_width=True, height=500)

    with tab6:
        st.subheader("Metrics Guide")
        st.markdown("""
        ### Key Formula Details

        **No ARR Band Filter:** The scorecard includes ALL instances regardless of ARR band, matching the Excel formulas exactly.

        **Tenure Filter:** Uses the '60+ Day Tenure?' column for eligible calculations.

        **AR Rates:** Only includes instances with > 0 automated resolutions to avoid skewing medians.

        **Grain:**
        - Customer metrics: CRM_ACCOUNT_ID (one per CRM account)
        - Instance metrics: INSTANCE_ACCOUNT_ID (one per instance)
        """)

# Footer
st.divider()
st.markdown("<div style='text-align: center; color: gray;'>AIAA Command Central • Formulas match Excel exactly</div>", unsafe_allow_html=True)
