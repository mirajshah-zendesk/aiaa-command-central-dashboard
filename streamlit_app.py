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
        'TOTAL_AUTOMATED_RESOLUTIONS', 'TOTAL_BOT_INTERACTIONS', 'TOTAL_CREATED_TICKETS_28D',
        'EMAIL_AUTOMATED_RESOLUTIONS', 'MSG_AUTOMATED_RESOLUTIONS',
        'EMAIL_BOT_INTERACTIONS', 'MSG_BOT_INTERACTIONS',
        'EMAIL_AR_RATE', 'MSG_AR_RATE', 'OVERALL_AR_RATE',
        'OVERALL_BOT_DEPLOYED_SHARE', 'ACTIVE_INTEGRATIONS_28D',
        'TOP_BOX_5_STAR_28D', 'TOTAL_RESPONSES_28D', 'TENURE_MONTHS',
        'AUTOMATED_RESOLUTIONS_NET_ARR_USD', 'ALLOWANCE_PERIOD_MONTHS',
        'DAYS_INTO_ALLOWANCE_CYCLE', 'TOTAL_ALLOWANCE',
        'PRORATED_ALLOWANCE_LAST_28D', 'AUTOMATED_RESOLUTIONS_USED_LAST_28D_NORMALIZED'
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
        penetrated_filter = snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True
        metrics['# customers'] = snapshot[penetrated_filter]['CRM_ACCOUNT_ID'].nunique()

        # "# instances" = # penetrated instances (Instance grain)
        metrics['# instances'] = snapshot[snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True]['INSTANCE_ACCOUNT_ID'].nunique()

        # Eligible (60+ day tenure) - Use '60+ Day Tenure?' column
        # This filter is used for both adopted AND eligible metrics
        eligible_tenure_filter = snapshot['60+ Day Tenure?'] == True

        # Adopted customers (CRM grain) - MUST have 60+ day tenure
        # Formula: COUNTUNIQUEIFS with ADOPTED=TRUE, PENETRATED=TRUE, 60+ Day Tenure=TRUE
        adopted_filter = (snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) & \
                        (snapshot['CRM_IS_AI_AGENTS_ADVANCED_ADOPTED'] == True) & \
                        eligible_tenure_filter
        metrics['Adopted customers'] = snapshot[adopted_filter]['CRM_ACCOUNT_ID'].nunique()

        # Adopted instances (Instance grain) - MUST have 60+ day tenure
        adopted_inst_filter = (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) & \
                              (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_ADOPTED'] == True) & \
                              eligible_tenure_filter
        metrics['Adopted instances'] = snapshot[adopted_inst_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # $100k+ ARR adopted customers
        adopted_100k_filter = adopted_filter & (snapshot['CRM_ARR_BAND_BROAD'] == 'c) 100K+')
        metrics['Adopted customers ($100k+)'] = snapshot[adopted_100k_filter]['CRM_ACCOUNT_ID'].nunique()

        # Eligible customers (CRM grain, 60+ day tenure)
        eligible_cust_filter = penetrated_filter & eligible_tenure_filter
        metrics['Eligible customers'] = snapshot[eligible_cust_filter]['CRM_ACCOUNT_ID'].nunique()

        # Eligible customers ($100k+)
        eligible_100k_filter = eligible_cust_filter & (snapshot['CRM_ARR_BAND_BROAD'] == 'c) 100K+')
        metrics['Eligible customers ($100k+)'] = snapshot[eligible_100k_filter]['CRM_ACCOUNT_ID'].nunique()

        # Eligible instances (Instance grain, 60+ day tenure)
        eligible_inst_filter = (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) & eligible_tenure_filter
        metrics['Eligible instances'] = snapshot[eligible_inst_filter]['INSTANCE_ACCOUNT_ID'].nunique()

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
        ar_filter = snapshot['OVERALL_AR_RATE'] > 0
        if ar_filter.sum() > 0:
            metrics['Median AR Rate'] = snapshot[ar_filter]['OVERALL_AR_RATE'].median()
        else:
            metrics['Median AR Rate'] = 0

        # AR Utilization Run Rate - Complex formula with multiple filters
        # Formula: SUM(AUTOMATED_RESOLUTIONS_USED_LAST_28D_NORMALIZED) / SUM(PRORATED_ALLOWANCE_LAST_28D)
        # Where: PENETRATED=TRUE, ARR>0, ALLOWANCE_PERIOD>=12, DAYS_INTO_CYCLE>28, TOTAL_ALLOWANCE<1M
        ar_util_filter = (
            (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
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
        email_ar_filter = snapshot['EMAIL_AR_RATE'] > 0
        if email_ar_filter.sum() > 0:
            metrics['Median AR Rate - Email'] = snapshot[email_ar_filter]['EMAIL_AR_RATE'].median()
        else:
            metrics['Median AR Rate - Email'] = 0

        msg_ar_filter = snapshot['MSG_AR_RATE'] > 0
        if msg_ar_filter.sum() > 0:
            metrics['Median AR Rate - Messaging'] = snapshot[msg_ar_filter]['MSG_AR_RATE'].median()
        else:
            metrics['Median AR Rate - Messaging'] = 0

        # AR Rate buckets - Count unique instances (not rows)
        # Formula: COUNTUNIQUEIFS with PENETRATED=TRUE, AR>0, AR<0.3
        ar_0_30_filter = (
            (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
            (snapshot['OVERALL_AR_RATE'] > 0) &
            (snapshot['OVERALL_AR_RATE'] < 0.3)
        )
        metrics['Instances AR 0-30%'] = snapshot[ar_0_30_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Formula: COUNTUNIQUEIFS with PENETRATED=TRUE, AR>=0.3
        ar_30_plus_filter = (
            (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
            (snapshot['OVERALL_AR_RATE'] >= 0.3)
        )
        metrics['Instances AR 30%+'] = snapshot[ar_30_plus_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Total active instances with bot deployed
        # Formula: COUNTIFS with PENETRATED=TRUE, FIRST_BOT_DEPLOYED_DATE<>""
        if 'FIRST_BOT_DEPLOYED_DATE' in snapshot.columns:
            bot_deployed_filter = (
                (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
                (snapshot['FIRST_BOT_DEPLOYED_DATE'].notna()) &
                (snapshot['FIRST_BOT_DEPLOYED_DATE'] != '')
            )
            metrics['Total active instances with bot deployed'] = bot_deployed_filter.sum()
        else:
            metrics['Total active instances with bot deployed'] = 0

        # Bot deployed share % - # bot interactions / # tickets
        total_tickets = snapshot['TOTAL_CREATED_TICKETS_28D'].sum()
        total_bot_interactions = snapshot['TOTAL_BOT_INTERACTIONS'].sum()
        if total_tickets > 0:
            metrics['Bot deployed share %'] = total_bot_interactions / total_tickets
        else:
            metrics['Bot deployed share %'] = 0

        # Store numerator and denominator for debugging
        metrics['Bot deployed share - numerator'] = total_bot_interactions
        metrics['Bot deployed share - denominator'] = total_tickets

        # Automated Resolutions
        metrics['Total ARs (28d)'] = snapshot['TOTAL_AUTOMATED_RESOLUTIONS'].sum()

        # Bot interactions
        metrics['Total Bot Interactions (28d)'] = snapshot['TOTAL_BOT_INTERACTIONS'].sum()

        # Tickets
        metrics['Total Tickets (28d)'] = snapshot['TOTAL_CREATED_TICKETS_28D'].sum()

        # BSAT
        total_responses = snapshot['TOTAL_RESPONSES_28D'].sum()
        top_box = snapshot['TOP_BOX_5_STAR_28D'].sum()
        metrics['Top Box BSAT %'] = (top_box / total_responses * 100) if total_responses > 0 else 0
        metrics['# Responses'] = total_responses
        metrics['# Top Box Responses'] = top_box

        # Integrations - Formula: COUNTUNIQUEIFS with PENETRATED=TRUE, 60+ Day Tenure=TRUE, ACTIVE_INTEGRATIONS_28D > 0
        integration_filter = (
            (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
            eligible_tenure_filter &
            (snapshot['ACTIVE_INTEGRATIONS_28D'] > 0)
        )
        metrics['# instances with integrations'] = snapshot[integration_filter]['INSTANCE_ACCOUNT_ID'].nunique()

        # Gen3 classification
        if 'GEN2_3_CLASSIFICATION' in snapshot.columns:
            metrics['Gen3 Instances'] = len(snapshot[snapshot['GEN2_3_CLASSIFICATION'] == 'Gen3'])
            metrics['Gen2 Instances'] = len(snapshot[snapshot['GEN2_3_CLASSIFICATION'] == 'Gen2'])
        else:
            metrics['Gen3 Instances'] = 0
            metrics['Gen2 Instances'] = 0

        # Go-live dates - Formula: COUNTUNIQUEIFS where date > previous_week AND date <= current_week
        one_week_ago = date - timedelta(days=7)

        if 'ACTUAL_GO_LIVE_DATE' in snapshot.columns:
            snapshot['ACTUAL_GO_LIVE_DATE'] = pd.to_datetime(snapshot['ACTUAL_GO_LIVE_DATE'], errors='coerce')
            actual_golive_filter = (
                (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
                (snapshot['ACTUAL_GO_LIVE_DATE'] > one_week_ago) &
                (snapshot['ACTUAL_GO_LIVE_DATE'] <= date)
            )
            metrics['Actual Go-Live (past week)'] = snapshot[actual_golive_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['Actual Go-Live (past week)'] = 0

        if 'PROJECTED_GO_LIVE_DATE' in snapshot.columns:
            snapshot['PROJECTED_GO_LIVE_DATE'] = pd.to_datetime(snapshot['PROJECTED_GO_LIVE_DATE'], errors='coerce')
            projected_golive_filter = (
                (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
                (snapshot['PROJECTED_GO_LIVE_DATE'] > one_week_ago) &
                (snapshot['PROJECTED_GO_LIVE_DATE'] <= date)
            )
            metrics['Projected Go-Live (next week)'] = snapshot[projected_golive_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['Projected Go-Live (next week)'] = 0

        # Bots deployed this week - Formula: COUNTUNIQUEIFS where FIRST_BOT_DEPLOYED_DATE > previous_week AND <= current_week
        if 'FIRST_BOT_DEPLOYED_DATE' in snapshot.columns:
            snapshot['FIRST_BOT_DEPLOYED_DATE'] = pd.to_datetime(snapshot['FIRST_BOT_DEPLOYED_DATE'], errors='coerce')
            bots_deployed_filter = (
                (snapshot['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True) &
                (snapshot['FIRST_BOT_DEPLOYED_DATE'] > one_week_ago) &
                (snapshot['FIRST_BOT_DEPLOYED_DATE'] <= date)
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

        # Filter for penetrated customers only
        penetrated_filter = snapshot['CRM_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True
        snapshot_penetrated = snapshot[penetrated_filter]

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
    st.header(":material/database: Data Source")

    st.markdown("### Snowflake Connection")
    st.info("Connected to: `presentation.success.ai_agents_advanced_command_central`")

    # Load data button
    if st.button("🔄 Load Data from Snowflake", use_container_width=True):
        with st.spinner("Loading data from Snowflake..."):
            df, error = load_data_from_snowflake()

            if error:
                st.error(f"Error loading data: {error}")
            elif df is not None:
                st.session_state.global_data = df
                st.session_state.last_load_time = datetime.now()
                st.success("✓ Data loaded successfully!")
                st.rerun()

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

    # Show data stats if loaded
    if st.session_state.global_data is not None:
        df = st.session_state.global_data
        st.success("✓ Data Loaded")

        if st.session_state.last_load_time:
            st.caption(f"Last loaded: {st.session_state.last_load_time.strftime('%Y-%m-%d %H:%M:%S')}")

        st.metric("Total Rows", f"{len(df):,}")

        if 'SOURCE_SNAPSHOT_DATE' in df.columns:
            df['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(df['SOURCE_SNAPSHOT_DATE'])
            st.metric("Date Range", f"{df['SOURCE_SNAPSHOT_DATE'].min().strftime('%Y-%m-%d')} to {df['SOURCE_SNAPSHOT_DATE'].max().strftime('%Y-%m-%d')}")

        if 'INSTANCE_ACCOUNT_SUBDOMAIN' in df.columns:
            st.metric("Unique Instances", df['INSTANCE_ACCOUNT_SUBDOMAIN'].nunique())

    if st.session_state.global_data is not None and len(st.session_state.global_data) > 0:
        st.divider()
        st.header("Filters")

        st.info("**Note:** Apply filters below to segment your data. Default shows all instances.")

        gdf = st.session_state.global_data.copy()
        gdf['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf['SOURCE_SNAPSHOT_DATE'])

        # Date filter
        if 'SOURCE_SNAPSHOT_DATE' in gdf.columns and not gdf['SOURCE_SNAPSHOT_DATE'].isna().all():
            try:
                min_date = gdf['SOURCE_SNAPSHOT_DATE'].min().date()
                max_date = gdf['SOURCE_SNAPSHOT_DATE'].max().date()

                date_range = st.date_input(
                    "Date Range",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date
                )
            except Exception as e:
                st.error(f"Error with date filter: {e}")
                date_range = None

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

        # AI Agents Product filter (multi-select)
        st.markdown("**AI Agents Product**")
        ai_product_options = st.multiselect(
            "Select product type(s)",
            options=['AI Agents Advanced', 'AI Agents Essentials'],
            default=['AI Agents Advanced', 'AI Agents Essentials'],
            label_visibility="collapsed",
            help="Advanced = INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED = TRUE; Essentials = Advanced is FALSE but INSTANCE_IS_AI_AGENTS_PAID_PENETRATED = TRUE",
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

    # Apply filters
    if 'date_range' in locals() and len(date_range) == 2:
        gdf = gdf[(gdf['SOURCE_SNAPSHOT_DATE'] >= pd.to_datetime(date_range[0])) &
                  (gdf['SOURCE_SNAPSHOT_DATE'] <= pd.to_datetime(date_range[1]))]

    if 'selected_region' in locals() and selected_region != 'All':
        gdf = gdf[gdf['CRM_REGION'] == selected_region]

    if 'selected_arr_band' in locals() and selected_arr_band != 'All':
        gdf = gdf[gdf['CRM_ARR_BAND_BROAD'] == selected_arr_band]

    if 'selected_responsibility' in locals() and selected_responsibility != 'All':
        gdf = gdf[gdf['RESPONSIBILITY'] == selected_responsibility]

    # AI Agents Product filter (multi-select with hierarchy)
    if 'ai_product_options' in locals() and len(ai_product_options) > 0:
        # Build filter based on selected options with hierarchy:
        # - Advanced: INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED = TRUE
        # - Essentials: Advanced = FALSE AND INSTANCE_IS_AI_AGENTS_PAID_PENETRATED = TRUE
        product_filter = pd.Series([False] * len(gdf), index=gdf.index)

        if 'AI Agents Advanced' in ai_product_options:
            # Advanced takes precedence
            product_filter |= (gdf['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == True)

        if 'AI Agents Essentials' in ai_product_options:
            # Essentials only if Advanced is False
            essentials_filter = (
                (gdf['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'] == False) &
                (gdf['INSTANCE_IS_AI_AGENTS_PAID_PENETRATED'] == True)
            )
            product_filter |= essentials_filter

        gdf = gdf[product_filter]

    # Calculate scorecard metrics
    with st.spinner("Calculating scorecard metrics..."):
        try:
            scorecard_df = calculate_scorecard_metrics(gdf)
        except Exception as e:
            st.error(f"Error calculating metrics: {e}")
            st.exception(e)
            st.stop()

    # Create tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        ":material/query_stats: Scorecard",
        ":material/trending_up: Trends",
        ":material/groups: Cohort Analysis",
        ":material/table: Data Explorer",
        ":material/info: Metrics Guide"
    ])

    with tab1:
        st.subheader("AIAA Command Central Scorecard")

        if len(scorecard_df) == 0:
            st.warning("No data available for the selected filters.")
        else:
            # Get latest metrics and calculate changes
            latest = scorecard_df.iloc[-1]
            latest_date = latest['Date'].strftime('%Y-%m-%d') if isinstance(latest['Date'], pd.Timestamp) else str(latest['Date'])

            st.markdown(f"**Latest Snapshot:** {latest_date} | **Total Snapshots:** {len(scorecard_df)}")

            # Debug: Show available dates and AR Util values
            with st.expander("🔍 Debug: Date Matching Info", expanded=False):
                debug_df = scorecard_df[['Date', 'AR Utilization Run Rate']].copy()
                debug_df['Date'] = pd.to_datetime(debug_df['Date'])
                debug_df = debug_df.sort_values('Date', ascending=False).head(10)
                debug_df['AR Util %'] = (debug_df['AR Utilization Run Rate'] * 100).round(2)
                st.dataframe(debug_df[['Date', 'AR Util %']], use_container_width=True)

                current_date = pd.to_datetime(latest['Date'])
                st.write(f"**Current Date:** {current_date.date()}")
                st.write(f"**1 Week Ago Target:** {(current_date - pd.Timedelta(days=7)).date()}")
                st.write(f"**4 Weeks Ago Target:** {(current_date - pd.Timedelta(days=28)).date()}")

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

            # Time series table
            st.markdown("### 📅 Time Series Data")

            display_cols = [
                'Date', '# customers', '# instances',
                'Adopted customers', 'Adopted instances',
                'Customer adoption %', 'Instance adoption %',
                'Median AR Rate', 'Bot deployed share %',
                'Total ARs (28d)', 'Top Box BSAT %'
            ]

            display_df = scorecard_df[display_cols].copy()
            display_df['Date'] = pd.to_datetime(display_df['Date']).dt.strftime('%Y-%m-%d')

            # Format percentages
            for col in ['Customer adoption %', 'Instance adoption %', 'Median AR Rate', 'Bot deployed share %', 'Top Box BSAT %']:
                if col in display_df.columns:
                    display_df[col] = display_df[col].apply(lambda x: f"{x:.1f}%")

            st.dataframe(display_df, use_container_width=True, height=400)

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

        # Calculate cohort metrics
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
        st.subheader("Data Explorer")

        all_columns = gdf.columns.tolist()
        default_cols = ['SOURCE_SNAPSHOT_DATE', 'INSTANCE_ACCOUNT_SUBDOMAIN', 'CRM_ACCOUNT_NAME', 'CRM_REGION', 'CRM_ARR_BAND_BROAD', 'OVERALL_AR_RATE']
        default_cols = [col for col in default_cols if col in all_columns]

        selected_columns = st.multiselect("Select columns", all_columns, default=default_cols if default_cols else all_columns[:10])

        if selected_columns:
            st.dataframe(gdf[selected_columns].head(1000), use_container_width=True, height=500)

    with tab5:
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
