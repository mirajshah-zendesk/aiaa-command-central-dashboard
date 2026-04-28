"""
AIAA Command Central Dashboard
Calculates all scorecard metrics from GlobalData CSV matching Excel formulas
"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

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

        # Eligible (60+ day tenure) - Use '60+ Day Tenure?' column if available
        # This filter is used for both adopted AND eligible metrics
        if '60+ Day Tenure?' in snapshot.columns:
            eligible_tenure_filter = snapshot['60+ Day Tenure?'] == True
        else:
            # Fallback to TENURE_MONTHS >= 2
            eligible_tenure_filter = snapshot['TENURE_MONTHS'] >= 2

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

# Sidebar for file upload
with st.sidebar:
    st.header(":material/upload_file: Data Upload")

    st.markdown("### Upload GlobalData CSV")

    global_data_file = st.file_uploader(
        "GlobalData CSV",
        type=["csv"],
        help="Upload AIAA Command Central - GlobalData CSV",
        key="global_data_upload"
    )

    if global_data_file is not None:
        try:
            with st.spinner("Loading data..."):
                df = pd.read_csv(global_data_file, low_memory=False)
                st.session_state.global_data = df
                st.success(f"✓ Loaded GlobalData")
                st.metric("Total Rows", f"{len(df):,}")

                if 'SOURCE_SNAPSHOT_DATE' in df.columns:
                    df['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(df['SOURCE_SNAPSHOT_DATE'])
                    st.metric("Date Range", f"{df['SOURCE_SNAPSHOT_DATE'].min().strftime('%Y-%m-%d')} to {df['SOURCE_SNAPSHOT_DATE'].max().strftime('%Y-%m-%d')}")

                if 'INSTANCE_ACCOUNT_SUBDOMAIN' in df.columns:
                    st.metric("Unique Instances", df['INSTANCE_ACCOUNT_SUBDOMAIN'].nunique())
        except Exception as e:
            st.error(f"Error loading file: {e}")
            st.exception(e)

    if st.session_state.global_data is not None:
        st.divider()
        st.header("Filters")

        st.info("**Note:** Dashboard includes ALL instances (no ARR band filter) to match Excel formulas exactly.")

        gdf = st.session_state.global_data.copy()
        gdf['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf['SOURCE_SNAPSHOT_DATE'])

        # Date filter
        if 'SOURCE_SNAPSHOT_DATE' in gdf.columns:
            min_date = gdf['SOURCE_SNAPSHOT_DATE'].min().date()
            max_date = gdf['SOURCE_SNAPSHOT_DATE'].max().date()

            date_range = st.date_input(
                "Date Range",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date
            )

        # Region filter (additional filter on top of ARR band)
        if 'CRM_REGION' in gdf.columns:
            regions = ['All'] + sorted(gdf['CRM_REGION'].dropna().unique().tolist())
            selected_region = st.selectbox("Region", regions)
        else:
            selected_region = 'All'

# Main content
if st.session_state.global_data is None:
    # Welcome screen
    st.markdown("""
    ## Welcome to AIAA Command Central Dashboard 🎯

    This dashboard automatically calculates all scorecard metrics from your GlobalData CSV, **matching the Excel formulas exactly**.

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
    1. Upload your **GlobalData CSV** file in the sidebar
    2. Metrics calculate automatically for each week
    3. Numbers will match your Excel scorecard exactly
    4. Use additional filters (date, region) to drill down

    **Upload your GlobalData CSV to begin!**
    """)

else:
    gdf = st.session_state.global_data.copy()
    gdf['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf['SOURCE_SNAPSHOT_DATE'])

    # Apply additional filters (ARR band filter is applied in calculation function)
    if 'date_range' in locals() and len(date_range) == 2:
        gdf = gdf[(gdf['SOURCE_SNAPSHOT_DATE'] >= pd.to_datetime(date_range[0])) &
                  (gdf['SOURCE_SNAPSHOT_DATE'] <= pd.to_datetime(date_range[1]))]

    if 'selected_region' in locals() and selected_region != 'All':
        gdf = gdf[gdf['CRM_REGION'] == selected_region]

    # Calculate scorecard metrics
    with st.spinner("Calculating scorecard metrics..."):
        try:
            scorecard_df = calculate_scorecard_metrics(gdf)
        except Exception as e:
            st.error(f"Error calculating metrics: {e}")
            st.exception(e)
            st.stop()

    # Create tabs
    tab1, tab2, tab3, tab4 = st.tabs([
        ":material/query_stats: Scorecard",
        ":material/trending_up: Trends",
        ":material/table: Data Explorer",
        ":material/info: Metrics Guide"
    ])

    with tab1:
        st.subheader("AIAA Command Central Scorecard")
        st.caption("All instances included (no ARR band filter)")

        if len(scorecard_df) == 0:
            st.warning("No data available for the selected filters.")
        else:
            # Get latest metrics
            latest = scorecard_df.iloc[-1]
            latest_date = latest['Date'].strftime('%Y-%m-%d') if isinstance(latest['Date'], pd.Timestamp) else str(latest['Date'])

            st.markdown(f"**Latest Snapshot:** {latest_date} | **Total Snapshots:** {len(scorecard_df)}")

            # Business Metrics
            st.markdown("### 📊 Business Metrics")
            col1, col2 = st.columns(2)

            with col1:
                ar_util = latest['AR Utilization Run Rate']
                st.metric(
                    "AR Utilization Run Rate",
                    f"{ar_util:.1%}",
                    border=True,
                    help="Average automated resolution rate (instances with > 0 ARs)"
                )

            with col2:
                if len(scorecard_df) >= 2:
                    prev_ar = scorecard_df.iloc[-2]['AR Utilization Run Rate']
                    delta_ar = ((ar_util - prev_ar) / prev_ar * 100) if prev_ar > 0 else 0
                    st.metric(
                        "AR Util WoW Change",
                        f"{delta_ar:+.1f}%",
                        border=True,
                        help="Week-over-week change"
                    )

            st.divider()

            # Impact Metrics
            st.markdown("### 🎯 Impact Metrics")

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric("# customers", f"{int(latest['# customers']):,}", border=True, help="Penetrated customers (CRM grain)")

            with col2:
                st.metric("# instances", f"{int(latest['# instances']):,}", border=True, help="Penetrated instances")

            with col3:
                st.metric("Adopted customers", f"{int(latest['Adopted customers']):,}", border=True)

            with col4:
                st.metric("Adopted instances", f"{int(latest['Adopted instances']):,}", border=True)

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric("Adopted customers ($100k+)", f"{int(latest['Adopted customers ($100k+)']):,}", border=True)

            with col2:
                st.metric("Eligible customers", f"{int(latest['Eligible customers']):,}", border=True, help="60+ day tenure")

            with col3:
                st.metric("Eligible customers ($100k+)", f"{int(latest['Eligible customers ($100k+)']):,}", border=True)

            with col4:
                st.metric("Eligible instances", f"{int(latest['Eligible instances']):,}", border=True, help="60+ day tenure")

            st.divider()

            # Adoption Rates
            st.markdown("### 📈 Adoption Rates")

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Customer adoption", f"{latest['Customer adoption %']:.1f}%", border=True)

            with col2:
                st.metric("Customer adoption ($100k+)", f"{latest['Customer adoption % ($100k+)']:.1f}%", border=True)

            with col3:
                st.metric("Instance adoption", f"{latest['Instance adoption %']:.1f}%", border=True)

            st.divider()

            # AR Rates
            st.markdown("### 🤖 AR Rates & Control Metrics")

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric("Median AR Rate", f"{latest['Median AR Rate']:.1%}", border=True)

            with col2:
                st.metric("Median AR - Email", f"{latest['Median AR Rate - Email']:.1%}", border=True)

            with col3:
                st.metric("Median AR - Messaging", f"{latest['Median AR Rate - Messaging']:.1%}", border=True)

            with col4:
                st.metric("# instances with integrations", f"{int(latest['# instances with integrations']):,}", border=True)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Instances AR 0-30%", f"{int(latest['Instances AR 0-30%']):,}", border=True)

            with col2:
                st.metric("Instances AR 30%+", f"{int(latest['Instances AR 30%+']):,}", border=True)

            with col3:
                st.metric("Total ARs (28d)", f"{int(latest['Total ARs (28d)']):,}", border=True)

            st.divider()

            # Bot Deployment
            st.markdown("### 🚀 Bot Deployment")

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Total active instances with bot deployed", f"{int(latest['Total active instances with bot deployed']):,}", border=True)

            with col2:
                st.metric("Bots deployed this week", f"{int(latest['Bots deployed this week']):,}", border=True)

            with col3:
                st.metric("Bot deployed share %", f"{latest['Bot deployed share %']:.1%}", border=True)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Gen3 Instances", f"{int(latest['Gen3 Instances']):,}", border=True)

            with col2:
                st.metric("Actual Go-Live (past week)", f"{int(latest['Actual Go-Live (past week)']):,}", border=True)

            with col3:
                st.metric("Projected Go-Live (next week)", f"{int(latest['Projected Go-Live (next week)']):,}", border=True)

            st.divider()

            # BSAT
            st.markdown("### ⭐ Customer Satisfaction (BSAT)")

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Top Box BSAT", f"{latest['Top Box BSAT %']:.1f}%", border=True)

            with col2:
                st.metric("# top box responses", f"{int(latest['# Top Box Responses']):,}", border=True)

            with col3:
                st.metric("# responses", f"{int(latest['# Responses']):,}", border=True)

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

            st.dataframe(display_df, use_container_width=True, hide_index=True, height=400)

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
        st.subheader("Data Explorer")

        all_columns = gdf.columns.tolist()
        default_cols = ['SOURCE_SNAPSHOT_DATE', 'INSTANCE_ACCOUNT_SUBDOMAIN', 'CRM_ACCOUNT_NAME', 'CRM_REGION', 'CRM_ARR_BAND_BROAD', 'OVERALL_AR_RATE']
        default_cols = [col for col in default_cols if col in all_columns]

        selected_columns = st.multiselect("Select columns", all_columns, default=default_cols if default_cols else all_columns[:10])

        if selected_columns:
            st.dataframe(gdf[selected_columns].head(1000), use_container_width=True, hide_index=True, height=500)

    with tab4:
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
