"""
AIA Command Central Dashboard
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
    page_title="AIA Command Central",
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

st.title(":material/analytics: AIA Command Central")

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
            INSTANCE_ACCOUNT_ID VARCHAR(255),
            INSTANCE_NAME VARCHAR(500),
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

def save_note(crm_account_id, crm_account_name, instance_account_id, instance_name, snapshot_date, notes, user_email):
    """Save or update a note for a customer"""
    try:
        session = get_active_session()

        # Format date consistently
        if isinstance(snapshot_date, pd.Timestamp):
            snapshot_date_str = snapshot_date.strftime('%Y-%m-%d')
        else:
            snapshot_date_str = str(snapshot_date)

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
                INSTANCE_ACCOUNT_ID = '{instance_account_id}',
                INSTANCE_NAME = $${instance_name}$$,
                UPDATED_AT = CURRENT_TIMESTAMP()
            WHERE CRM_ACCOUNT_ID = '{crm_account_id}'
              AND SNAPSHOT_DATE = '{snapshot_date_str}'::DATE
            """
            session.sql(update_sql).collect()
        else:
            # Insert new record
            insert_sql = f"""
            INSERT INTO STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
            (CRM_ACCOUNT_ID, CRM_ACCOUNT_NAME, INSTANCE_ACCOUNT_ID, INSTANCE_NAME, SNAPSHOT_DATE, NOTES, CREATED_BY, UPDATED_AT)
            VALUES (
                '{crm_account_id}',
                $${crm_account_name}$$,
                '{instance_account_id}',
                $${instance_name}$$,
                '{snapshot_date_str}'::DATE,
                $${notes}$$,
                '{user_email}',
                CURRENT_TIMESTAMP()
            )
            """
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
        SELECT CRM_ACCOUNT_ID, CRM_ACCOUNT_NAME, INSTANCE_ACCOUNT_ID, INSTANCE_NAME, NOTES, CREATED_BY, UPDATED_AT
        FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ADOPTION_LOSS_NOTES
        WHERE SNAPSHOT_DATE = '{snapshot_date_str}'::DATE
        """
        df = session.sql(query).to_pandas()
        return df
    except Exception as e:
        # Table might not exist yet or other error
        st.warning(f"Could not load notes: {e}")
        return pd.DataFrame(columns=['CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME', 'INSTANCE_ACCOUNT_ID', 'INSTANCE_NAME', 'NOTES', 'CREATED_BY', 'UPDATED_AT'])

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

@st.cache_data(ttl=3600)
def load_aie_project_health():
    """
    One row per CRM account with the canonical AI Expert project's
    zd_health_summary_c. Tiebreakers (in order):
      1. Currently active (start <= today AND (end IS NULL OR end >= today))
      2. More submitted hours
      3. Most recently started
      4. Most recently created
    """
    try:
        session = get_active_session()
        query = """
        WITH ranked AS (
            SELECT
                pse_account_c                        AS crm_account_id,
                zd_health_summary_c,
                ROW_NUMBER() OVER (
                    PARTITION BY pse_account_c
                    ORDER BY
                        CASE
                            WHEN pse_start_date_c <= CURRENT_DATE()
                             AND (pse_end_date_c IS NULL OR pse_end_date_c >= CURRENT_DATE())
                            THEN 0 ELSE 1
                        END,
                        pse_total_submitted_hours_c DESC NULLS LAST,
                        pse_start_date_c DESC NULLS LAST,
                        created_date DESC
                ) AS rn
            FROM cleansed.salesforce.salesforce_pse_proj_c_bcv
            WHERE name ILIKE '%AI Expert%'
              AND pse_account_c IS NOT NULL
        )
        SELECT crm_account_id, zd_health_summary_c
        FROM ranked
        WHERE rn = 1
        """
        df = session.sql(query).to_pandas()
        return df, None
    except Exception as e:
        return None, str(e)

def load_icl_notes(snapshot_date):
    """All ICL notes for a given snapshot date — one row per instance."""
    try:
        session = get_active_session()
        snapshot_date_str = pd.Timestamp(snapshot_date).strftime('%Y-%m-%d')
        df = session.sql(f"""
            SELECT instance_account_id, note, updated_by, updated_at
            FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ICL_NOTES
            WHERE snapshot_date = '{snapshot_date_str}'::DATE
        """).to_pandas()
        return df, None
    except Exception as e:
        return None, str(e)

def upsert_icl_note(instance_account_id, snapshot_date, note, user_email):
    """Insert or update a single ICL note."""
    try:
        session = get_active_session()
        snapshot_date_str = pd.Timestamp(snapshot_date).strftime('%Y-%m-%d')
        # MERGE pattern: emulate UPSERT.
        check = session.sql(f"""
            SELECT COUNT(*) AS cnt
            FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ICL_NOTES
            WHERE instance_account_id = '{instance_account_id}'
              AND snapshot_date = '{snapshot_date_str}'::DATE
        """).collect()
        exists = check[0]['CNT'] > 0
        if exists:
            session.sql(f"""
                UPDATE STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ICL_NOTES
                SET note = $${note}$$,
                    updated_by = '{user_email}',
                    updated_at = CURRENT_TIMESTAMP()
                WHERE instance_account_id = '{instance_account_id}'
                  AND snapshot_date = '{snapshot_date_str}'::DATE
            """).collect()
        else:
            session.sql(f"""
                INSERT INTO STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.ICL_NOTES
                  (instance_account_id, snapshot_date, note, updated_by, updated_at)
                VALUES (
                    '{instance_account_id}',
                    '{snapshot_date_str}'::DATE,
                    $${note}$$,
                    '{user_email}',
                    CURRENT_TIMESTAMP()
                )
            """).collect()
        return True, None
    except Exception as e:
        return False, str(e)

@st.cache_data(ttl=3600)
def load_integrated_cohort_overrides():
    """
    One row per CRM with manually-curated delay_code and Q2 target flag.
    Sourced from STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.INTEGRATED_COHORT_OVERRIDES
    which is loaded from a CSV upload — re-upload to the OVERRIDES_STAGE and
    rebuild that table when the data changes.
    """
    try:
        session = get_active_session()
        query = """
        SELECT crm_account_id, q2_target_account, delay_code
        FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.INTEGRATED_COHORT_OVERRIDES
        """
        df = session.sql(query).to_pandas()
        return df, None
    except Exception as e:
        return None, str(e)

# SPIFF Leaderboard scoring constants (March 25 -> July 31, 2026 window)
SPIFF_WINDOW_START = '2026-03-25'
SPIFF_WINDOW_END = '2026-07-31'
SPIFF_POINTS_DEPLOYED = 1
SPIFF_POINTS_ADOPTED = 5
SPIFF_POINTS_AR50 = 3

# Maps mart-side name variants -> canonical team-mapping name. Add new entries
# as typos / aliases / middle-name issues are discovered.
SPIFF_NAME_ALIASES = {
    'Harvey Hind-Pichter': 'Harvey Hind-Pitcher',  # typo in upstream data
}

# Names excluded from the leaderboard entirely (e.g., partner / subco resources).
SPIFF_EXCLUDED_NAMES = {
    'Laura Petri',
    'Gustavo Prezoto',
}

@st.cache_data(ttl=300)
def load_spiff_team_mapping(_v=3):
    """One row per person with their team and role (AI Strategist or AI Consultant).

    The _v param is a cache-buster: bump when the underlying table changes.
    """
    try:
        session = get_active_session()
        query = """
        SELECT full_name, team, role
        FROM STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.SPIFF_TEAM_MAPPING
        WHERE full_name IS NOT NULL
        """
        df = session.sql(query).to_pandas()
        # Defensive: ensure column names are uppercase (Snowflake default).
        df.columns = [c.upper() for c in df.columns]
        return df, None
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=300)
def load_third_party_bot_first_seen(_v=2):
    """One row per (crm_account_id, third_party_ai_bot) with first/last seen
    dates. Used by the New Third-Party Bot Signals tab to identify NEW
    (specific bot, CRM) pairs whose first appearance is recent.
    """
    try:
        session = get_active_session()
        query = """
        SELECT
            crm_account_id,
            third_party_ai_bot,
            MIN(source_snapshot_date) AS first_seen_date,
            MAX(source_snapshot_date) AS last_seen_date
        FROM functional.product_analytics.third_party_bot_usage_crm_daily_snapshot
        GROUP BY 1, 2
        """
        df = session.sql(query).to_pandas()
        df.columns = [c.upper() for c in df.columns]
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
        'VERIFIED_AUTOMATED_RESOLUTION_RATE_PAID',
        'TOTAL_INTENT_COUNT_28D', 'PROCEDURES_COUNT_28D', 'DIALOGUE_FLOWS_COUNT_28D'
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

        # Dialogue Flows - instances with dialogue flows configured
        if 'DIALOGUE_FLOWS_COUNT_28D' in snapshot.columns:
            dialogue_flows_filter = snapshot['DIALOGUE_FLOWS_COUNT_28D'] > 0
            metrics['# instances with dialogue flows'] = snapshot[dialogue_flows_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['# instances with dialogue flows'] = 0

        # Procedures - instances with procedures configured
        if 'PROCEDURES_COUNT_28D' in snapshot.columns:
            procedures_filter = snapshot['PROCEDURES_COUNT_28D'] > 0
            metrics['# instances with procedures'] = snapshot[procedures_filter]['INSTANCE_ACCOUNT_ID'].nunique()
        else:
            metrics['# instances with procedures'] = 0

        # Total penetrated instances (for percentage calculations)
        metrics['Total penetrated instances'] = snapshot['INSTANCE_ACCOUNT_ID'].nunique()

        # Calculate percentages
        if metrics['Total penetrated instances'] > 0:
            metrics['% instances with integrations'] = (metrics['# instances with integrations'] / metrics['Total penetrated instances']) * 100
            metrics['% instances with dialogue flows'] = (metrics['# instances with dialogue flows'] / metrics['Total penetrated instances']) * 100
            metrics['% instances with procedures'] = (metrics['# instances with procedures'] / metrics['Total penetrated instances']) * 100
        else:
            metrics['% instances with integrations'] = 0
            metrics['% instances with dialogue flows'] = 0
            metrics['% instances with procedures'] = 0

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
    Calculate customer and instance counts by cohort for each snapshot date.
    Returns a dataframe with Date, Cohort, # Customers (distinct CRMs), and # Instances (rows).
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

        cohort_counts = snapshot_penetrated.groupby('COHORT').agg(
            **{
                '# Customers': ('CRM_ACCOUNT_ID', 'nunique'),
                '# Instances': ('INSTANCE_ACCOUNT_ID', 'nunique'),
            }
        ).reset_index()
        cohort_counts.columns = ['Cohort', '# Customers', '# Instances']
        cohort_counts['Date'] = date

        # Exclude "not penetrated" cohort
        cohort_counts = cohort_counts[~cohort_counts['Cohort'].str.lower().str.contains('not penetrated', na=False)]

        cohort_metrics_list.append(cohort_counts)

    if len(cohort_metrics_list) == 0:
        return pd.DataFrame(columns=['Date', 'Cohort', '# Customers', '# Instances'])

    # Combine all snapshots
    cohort_df = pd.concat(cohort_metrics_list, ignore_index=True)
    cohort_df = cohort_df[['Date', 'Cohort', '# Customers', '# Instances']]

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

        # ARR Band filter (multi-select — defaults to >=12K, hiding the
        # noisy <12K band but letting users add it back if they want)
        if 'CRM_ARR_BAND_BROAD' in gdf.columns:
            arr_values = gdf['CRM_ARR_BAND_BROAD'].dropna().unique()
            arr_bands = sorted([str(a) for a in arr_values if a is not None])
            arr_default = [b for b in arr_bands if b in ('b) 12K-100K', 'c) 100K+')]
            selected_arr_bands = st.multiselect(
                "ARR Band", arr_bands, default=arr_default, key="arr_filter",
                help="Defaults to 12K+ ARR bands. Add `<12K` to include all customers, or remove a band to narrow further.",
            )
        else:
            selected_arr_bands = []

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

        # AI Expert Project filter — does the CRM have an AIE project?
        # Uses the same per-CRM project resolver as the ICL tab.
        selected_aie_project = st.selectbox(
            "AI Expert Project",
            options=['All', 'Has AIE Project', 'No AIE Project'],
            index=0,
            key="aie_project_filter",
            help="Filter to CRMs with (or without) an AI Expert project. "
                 "A CRM 'has' a project if it has any row in salesforce_pse_proj_c_bcv "
                 "with name ILIKE '%AI Expert%' (canonical project picked by the same "
                 "tiebreaker as the ICL tab).",
        )

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
    ## Welcome to AIA Command Central Dashboard 🎯

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
    - **AI Agent Deployment**: AI Agent deployment stats, Gen2/Gen3 classification
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

    if 'selected_arr_bands' in locals() and selected_arr_bands:
        gdf = gdf[gdf['CRM_ARR_BAND_BROAD'].isin(selected_arr_bands)]

    if 'selected_responsibility' in locals() and selected_responsibility != 'All':
        gdf = gdf[gdf['RESPONSIBILITY'] == selected_responsibility]

    if 'selected_segment' in locals() and selected_segment != 'All':
        gdf = gdf[gdf['CRM_MARKET_SEGMENT'] == selected_segment]

    if 'selected_subregion' in locals() and selected_subregion != 'All':
        gdf = gdf[gdf['CRM_SUB_REGION'] == selected_subregion]

    if 'selected_industry' in locals() and selected_industry != 'All':
        gdf = gdf[gdf['CRM_INDUSTRY'] == selected_industry]

    # AI Expert Project filter — membership test against the canonical
    # one-row-per-CRM AIE project set.
    if 'selected_aie_project' in locals() and selected_aie_project != 'All':
        aie_health_df, _ = load_aie_project_health()
        if aie_health_df is not None and 'CRM_ACCOUNT_ID' in aie_health_df.columns:
            aie_crms = set(aie_health_df['CRM_ACCOUNT_ID'].astype(str).str.strip())
        else:
            aie_crms = set()
        crm_ids = gdf['CRM_ACCOUNT_ID'].astype(str).str.strip()
        if selected_aie_project == 'Has AIE Project':
            gdf = gdf[crm_ids.isin(aie_crms)]
        elif selected_aie_project == 'No AIE Project':
            gdf = gdf[~crm_ids.isin(aie_crms)]

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
    tab1, tab2, tab3, tab4, tab_missed_targets, tab5, tab_icl, tab_3pb, tab6, tab7, tab_spiff = st.tabs([
        ":material/query_stats: Scorecard",
        ":material/trending_up: Trends",
        ":material/groups: Cohort Analysis",
        ":material/warning: Adoption Loss",
        ":material/schedule: Missed Target Dates",
        ":material/table: Data Explorer",
        ":material/list_alt: Integrated Cohort List",
        ":material/smart_toy: New Third-Party Bot Signals",
        ":material/info: Metrics Guide",
        ":material/rocket_launch: Kickoff Analysis",
        ":material/emoji_events: SPIFF Leaderboard"
    ])

    with tab1:
        st.subheader("AIA Command Central Scorecard")

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
                    ("Instances AR 0-30%", "Instances AR 0-30%", "number"),
                    ("Instances AR 30%+", "Instances AR 30%+", "number"),
                    ("Total ARs (28d)", "Total ARs (28d)", "number"),
                ],
                "🔧 Integrations, Dialogue Flows & Procedures": [
                    ("# Instances with Integrations", "# instances with integrations", "number"),
                    ("% Instances with Integrations", "% instances with integrations", "percent_decimal"),
                    ("# Instances with Dialogue Flows", "# instances with dialogue flows", "number"),
                    ("% Instances with Dialogue Flows", "% instances with dialogue flows", "percent_decimal"),
                    ("# Instances with Procedures", "# instances with procedures", "number"),
                    ("% Instances with Procedures", "% instances with procedures", "percent_decimal"),
                    ("Total Penetrated Instances", "Total penetrated instances", "number"),
                ],
                "✅ Verified Resolution Quality": [
                    ("Poor (<50% Verified)", "Customers - Poor Verified (<50%)", "number"),
                    ("Acceptable (50-80% Verified)", "Customers - Acceptable Verified (50-80%)", "number"),
                    ("Optimal (>80% Verified)", "Customers - Optimal Verified (>80%)", "number"),
                ],
                "🚀 AI Agent Deployment": [
                    ("Total Active Instances with AI Agent", "Total active instances with bot deployed", "number"),
                    ("AI Agents Deployed This Week", "Bots deployed this week", "number"),
                    ("AI Agent Deployed Share %", "Bot deployed share %", "percent"),
                    ("AI Agent Interactions (28d)", "Bot deployed share - numerator", "number"),
                    ("Total Tickets (28d)", "Bot deployed share - denominator", "number"),
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
            # Use the selected date if available, otherwise get latest date
            if display_selected_date is not None:
                latest_date = pd.to_datetime(display_selected_date)
            else:
                latest_date = cohort_df['Date'].max()

            latest_cohorts = cohort_df[cohort_df['Date'] == latest_date].copy()

            if len(latest_cohorts) == 0:
                st.warning(f"No cohort data available for {latest_date.strftime('%Y-%m-%d')}. Please select a different date.")
            else:
                st.markdown(f"**Snapshot Date:** {latest_date.strftime('%Y-%m-%d') if isinstance(latest_date, pd.Timestamp) else str(latest_date)}")

                # Helper function to calculate cohort changes as percentage points
                def calculate_cohort_changes(cohort_name, value_col='# Customers'):
                    cohort_data = cohort_df[cohort_df['Cohort'] == cohort_name].copy()
                    cohort_data['Date'] = pd.to_datetime(cohort_data['Date'])
                    cohort_data = cohort_data.sort_values('Date')

                    if len(cohort_data) == 0:
                        return None, None, None, None

                    # Get current value
                    current_row = cohort_data[cohort_data['Date'] == latest_date]
                    if len(current_row) == 0:
                        return None, None, None, None

                    current = current_row.iloc[0][value_col]

                    # Calculate current % of total
                    total_current = cohort_df[cohort_df['Date'] == latest_date][value_col].sum()
                    current_pct = (current / total_current * 100) if total_current > 0 else 0

                    # WoW change (exact match for 7 days ago) - as percentage points
                    wow_change = None
                    one_week_ago = latest_date - pd.Timedelta(days=7)
                    prev_week_data = cohort_data[cohort_data['Date'] == one_week_ago]
                    if len(prev_week_data) > 0:
                        prev_week = prev_week_data.iloc[0][value_col]
                        total_prev_week = cohort_df[cohort_df['Date'] == one_week_ago][value_col].sum()
                        if not pd.isna(prev_week) and total_prev_week > 0:
                            prev_week_pct = (prev_week / total_prev_week * 100)
                            wow_change = current_pct - prev_week_pct

                    # 4-week change (exact match for 28 days ago) - as percentage points
                    four_week_change = None
                    four_weeks_ago = latest_date - pd.Timedelta(days=28)
                    four_week_data = cohort_data[cohort_data['Date'] == four_weeks_ago]
                    if len(four_week_data) > 0:
                        four_week_val = four_week_data.iloc[0][value_col]
                        total_four_weeks = cohort_df[cohort_df['Date'] == four_weeks_ago][value_col].sum()
                        if not pd.isna(four_week_val) and total_four_weeks > 0:
                            four_week_pct = (four_week_val / total_four_weeks * 100)
                            four_week_change = current_pct - four_week_pct

                    # QTD change (quarter-to-date) - as percentage points
                    qtd_change = None
                    quarter_start = pd.Timestamp(latest_date.year, ((latest_date.quarter - 1) * 3) + 1, 1)
                    qtd_data = cohort_data[cohort_data['Date'] >= quarter_start].sort_values('Date')
                    if len(qtd_data) >= 2:
                        qtd_first = qtd_data.iloc[0][value_col]
                        qtd_first_date = qtd_data.iloc[0]['Date']
                        total_qtd_first = cohort_df[cohort_df['Date'] == qtd_first_date][value_col].sum()
                        if not pd.isna(qtd_first) and total_qtd_first > 0:
                            qtd_first_pct = (qtd_first / total_qtd_first * 100)
                            qtd_change = current_pct - qtd_first_pct

                    # Format values
                    current_str = f"{int(current):,}"
                    wow_str = f"{wow_change:+.1f}pp" if wow_change is not None else "—"
                    four_week_str = f"{four_week_change:+.1f}pp" if four_week_change is not None else "—"
                    qtd_str = f"{qtd_change:+.1f}pp" if qtd_change is not None else "—"

                    return current_str, wow_str, four_week_str, qtd_str

                # Build cohort table
                st.markdown("### 📊 Customer & Instance Counts by Cohort")
                st.caption("**# Customers** = distinct CRM accounts in the cohort. **# Instances** = distinct instance subdomains in the cohort.")

                table_data = []
                # First pass: collect raw numbers for percentage calculation
                cohort_raw_customers = {}
                cohort_raw_instances = {}
                for cohort in sorted(latest_cohorts['Cohort'].unique()):
                    cohort_data = latest_cohorts[latest_cohorts['Cohort'] == cohort]
                    if len(cohort_data) > 0:
                        cohort_raw_customers[cohort] = cohort_data.iloc[0]['# Customers']
                        cohort_raw_instances[cohort] = cohort_data.iloc[0]['# Instances']

                total_customers = sum(cohort_raw_customers.values())
                total_instances = sum(cohort_raw_instances.values())

                # Second pass: build table with percentages
                for cohort in sorted(latest_cohorts['Cohort'].unique()):
                    cust_current, cust_wow, cust_four_week, cust_qtd = calculate_cohort_changes(cohort, '# Customers')
                    inst_current, inst_wow, inst_four_week, inst_qtd = calculate_cohort_changes(cohort, '# Instances')
                    if cust_current is not None:
                        raw_cust = cohort_raw_customers.get(cohort, 0)
                        raw_inst = cohort_raw_instances.get(cohort, 0)
                        cust_pct = (raw_cust / total_customers * 100) if total_customers > 0 else 0
                        inst_pct = (raw_inst / total_instances * 100) if total_instances > 0 else 0

                        table_data.append({
                            "Cohort": cohort,
                            "# Customers": cust_current,
                            "Customer % of Total": f"{cust_pct:.1f}%",
                            "Customer WoW": cust_wow,
                            "Customer 4-Week": cust_four_week,
                            "Customer QTD": cust_qtd,
                            "# Instances": inst_current,
                            "Instance % of Total": f"{inst_pct:.1f}%",
                            "Instance WoW": inst_wow,
                            "Instance 4-Week": inst_four_week,
                            "Instance QTD": inst_qtd,
                        })

                if len(table_data) > 0:
                    cohort_table = pd.DataFrame(table_data)
                    cohort_table = cohort_table.set_index('Cohort')
                    st.dataframe(cohort_table, use_container_width=True, height=min(len(table_data) * 35 + 38, 600))
                else:
                    st.warning("No cohort data available for the selected snapshot.")

                st.divider()

                # Time series table
                st.markdown("### 📅 Cohort Trends Over Time")

                metric_choice = st.radio(
                    "Metric",
                    options=['# Customers', '# Instances'],
                    horizontal=True,
                    key='cohort_trend_metric',
                )

                cohort_pivot = cohort_df.pivot(index='Date', columns='Cohort', values=metric_choice)
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

                    # Compute the "driving instance" per CRM — the instance whose
                    # AR rate dropped the most from previous to latest snapshot.
                    # Tiebreaker: lowest current AR rate. Used to populate the
                    # representative instance subdomain in the main table.
                    inst_pair = lost_customers_latest[['CRM_ACCOUNT_ID', 'INSTANCE_ACCOUNT_ID',
                                                       'INSTANCE_ACCOUNT_SUBDOMAIN', 'AR_RATE_PAID']].rename(
                        columns={'AR_RATE_PAID': 'AR_RATE_LATEST'}
                    )
                    inst_prev = lost_customers_prev[['CRM_ACCOUNT_ID', 'INSTANCE_ACCOUNT_ID',
                                                     'AR_RATE_PAID']].rename(
                        columns={'AR_RATE_PAID': 'AR_RATE_PREV'}
                    )
                    inst_pair = inst_pair.merge(
                        inst_prev,
                        on=['CRM_ACCOUNT_ID', 'INSTANCE_ACCOUNT_ID'],
                        how='left',
                    )
                    inst_pair['AR_RATE_DROP'] = inst_pair['AR_RATE_PREV'].fillna(0) - inst_pair['AR_RATE_LATEST'].fillna(0)
                    # Pick the instance per CRM with the largest positive drop;
                    # tiebreak on lowest current AR rate.
                    inst_pair = inst_pair.sort_values(
                        ['CRM_ACCOUNT_ID', 'AR_RATE_DROP', 'AR_RATE_LATEST'],
                        ascending=[True, False, True],
                    )
                    driving_instance = inst_pair.drop_duplicates(subset='CRM_ACCOUNT_ID', keep='first')[
                        ['CRM_ACCOUNT_ID', 'INSTANCE_ACCOUNT_ID', 'INSTANCE_ACCOUNT_SUBDOMAIN']
                    ]

                    # Aggregate metrics by CRM account for both snapshots
                    # Latest snapshot — note that INSTANCE_* fields are NOT
                    # aggregated here; they're merged in from `driving_instance`
                    # below so we display the actual loss-driving instance.
                    lost_latest_agg = lost_customers_latest.groupby('CRM_ACCOUNT_ID').agg({
                        'CRM_ACCOUNT_NAME': 'first',
                        'CRM_REGION': 'first',
                        'CRM_ARR_BAND_BROAD': 'first',
                        'CRM_MARKET_SEGMENT': 'first',
                        'AI_STRATEGIST_NAME': 'first',  # AI Success Strategist
                        'CONSULTANT_NAME': 'first',  # AI Expert Consultant/CSM
                        'AR_RATE_PAID': 'mean',  # Average AR rate across instances
                        'AUTOMATED_RESOLUTIONS_PAID': 'sum',  # Sum ARs across instances
                        'BOT_INTERACTIONS_PAID': 'sum'  # Sum bot interactions across instances
                    }).reset_index().merge(driving_instance, on='CRM_ACCOUNT_ID', how='left')

                    # Previous snapshot
                    lost_prev_agg = lost_customers_prev.groupby('CRM_ACCOUNT_ID').agg({
                        'AR_RATE_PAID': 'mean',
                        'AUTOMATED_RESOLUTIONS_PAID': 'sum',
                        'BOT_INTERACTIONS_PAID': 'sum'
                    }).reset_index()

                    # Merge current and previous data
                    lost_df = lost_latest_agg.merge(
                        lost_prev_agg,
                        on='CRM_ACCOUNT_ID',
                        how='left',
                        suffixes=('_Current', '_Previous')
                    )

                    # Rename columns for clarity with shortened names. The
                    # AR Rate columns are means across all instances of the CRM
                    # — flagged in the column label so the rollup is explicit.
                    lost_df = lost_df.rename(columns={
                        'CRM_ACCOUNT_NAME': 'Account',
                        'INSTANCE_ACCOUNT_SUBDOMAIN': 'Driving Instance',
                        'CRM_REGION': 'Region',
                        'CRM_ARR_BAND_BROAD': 'ARR Band',
                        'CRM_MARKET_SEGMENT': 'Segment',
                        'AI_STRATEGIST_NAME': 'AI Strategist',
                        'CONSULTANT_NAME': 'CSM',
                        'AR_RATE_PAID_Current': 'Avg AR Rate (Current)',
                        'AR_RATE_PAID_Previous': 'Avg AR Rate (Previous)',
                        'AUTOMATED_RESOLUTIONS_PAID_Current': 'Total ARs (Current, 28d)',
                        'AUTOMATED_RESOLUTIONS_PAID_Previous': 'Total ARs (Previous, 28d)',
                        'BOT_INTERACTIONS_PAID_Current': 'Current Bot Interactions',
                        'BOT_INTERACTIONS_PAID_Previous': 'Previous Bot Interactions'
                    })

                    # Calculate changes for categorization
                    lost_df['AR_Rate_PP_Change'] = (lost_df['Avg AR Rate (Current)'] - lost_df['Avg AR Rate (Previous)'])
                    lost_df['AR_Count_Change'] = lost_df['Total ARs (Current, 28d)'] - lost_df['Total ARs (Previous, 28d)']
                    lost_df['Bot_Interactions_Pct_Change'] = (
                        (lost_df['Current Bot Interactions'] - lost_df['Previous Bot Interactions']) /
                        lost_df['Previous Bot Interactions'].replace(0, 1)  # Avoid division by zero
                    )

                    # Apply waterfall categorization logic
                    def categorize_loss(row):
                        # 1. No Longer Activated: fewer than 100 ARs
                        if row['Total ARs (Current, 28d)'] < 100:
                            return "No Longer Activated"

                        # 2. Increased Deployment: AR count hasn't dropped but bot interactions increased >20%
                        if row['AR_Count_Change'] >= 0 and row['Bot_Interactions_Pct_Change'] > 0.20:
                            return "Increased Deployment"

                        # 3. Performance & Volume Decline: AR rate down >5pp AND bot interactions reduced >20%
                        if row['AR_Rate_PP_Change'] < -0.05 and row['Bot_Interactions_Pct_Change'] < -0.20:
                            return "Performance & Volume Decline"

                        # 4. Performance Decline: AR rate down >5pp only
                        if row['AR_Rate_PP_Change'] < -0.05:
                            return "Performance Decline"

                        # 5. Yoyo: AR rate is >25%
                        if row['Avg AR Rate (Current)'] > 0.25:
                            return "Yoyo"

                        return "Other"

                    lost_df['Category'] = lost_df.apply(categorize_loss, axis=1)

                    # Initialize notes table
                    init_notes_table()

                    # Load existing notes for this snapshot
                    existing_notes = load_notes(latest_date)
                    notes_dict = dict(zip(existing_notes['CRM_ACCOUNT_ID'], existing_notes['NOTES'])) if len(existing_notes) > 0 else {}

                    # Merge notes into the dataframe
                    lost_df['Notes'] = lost_df['CRM_ACCOUNT_ID'].map(notes_dict).fillna('')

                    # Reorder columns (keep IDs and calculation columns for internal use)
                    column_order = [
                        'CRM_ACCOUNT_ID', 'Account', 'INSTANCE_ACCOUNT_ID', 'Driving Instance',
                        'Category', 'Region', 'ARR Band', 'Segment', 'AI Strategist', 'CSM',
                        'Avg AR Rate (Current)', 'Avg AR Rate (Previous)',
                        'Total ARs (Current, 28d)', 'Total ARs (Previous, 28d)',
                        'Current Bot Interactions', 'Previous Bot Interactions',
                        'AR_Rate_PP_Change', 'AR_Count_Change', 'Bot_Interactions_Pct_Change',
                        'Notes'
                    ]
                    lost_df = lost_df[column_order].sort_values('Account')

                    # Create display dataframe with formatted values
                    lost_df_display = lost_df.copy()
                    lost_df_display['Avg AR Rate (Current)'] = lost_df_display['Avg AR Rate (Current)'].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
                    lost_df_display['Avg AR Rate (Previous)'] = lost_df_display['Avg AR Rate (Previous)'].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
                    lost_df_display['Total ARs (Current, 28d)'] = lost_df_display['Total ARs (Current, 28d)'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "0")
                    lost_df_display['Total ARs (Previous, 28d)'] = lost_df_display['Total ARs (Previous, 28d)'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "0")

                    st.caption(
                        "Each row is a CRM account. **Driving Instance** is the instance whose AR rate dropped "
                        "the most week-over-week (or the only instance, if there's just one). AR Rate columns "
                        "are **averaged** across all instances of the CRM and AR counts are **summed** — "
                        "expand the per-instance breakdown below to see every instance."
                    )

                    # Select columns for display (CRM-level + driving instance)
                    display_columns = [
                        'Account', 'Driving Instance', 'Category', 'Region', 'ARR Band', 'Segment',
                        'AI Strategist', 'CSM',
                        'Avg AR Rate (Current)', 'Avg AR Rate (Previous)',
                        'Total ARs (Current, 28d)', 'Total ARs (Previous, 28d)'
                    ]
                    st.dataframe(lost_df_display[display_columns], use_container_width=True, height=400, hide_index=True)

                    # ===== Per-instance breakdown =====
                    with st.expander(":material/zoom_in: Per-instance breakdown", expanded=False):
                        st.caption(
                            "Per-instance AR rate and AR count for each CRM in the table above. Use this to see which "
                            "instance(s) drove the CRM-level change."
                        )
                        # Build the per-instance comparison dataframe
                        inst_cols = ['CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME', 'INSTANCE_ACCOUNT_SUBDOMAIN',
                                     'AR_RATE_PAID', 'AUTOMATED_RESOLUTIONS_PAID']
                        inst_latest = lost_customers_latest[inst_cols].rename(columns={
                            'AR_RATE_PAID': 'AR Rate (Current)',
                            'AUTOMATED_RESOLUTIONS_PAID': 'ARs (Current, 28d)',
                        })
                        inst_prev = lost_customers_prev[['CRM_ACCOUNT_ID', 'INSTANCE_ACCOUNT_SUBDOMAIN',
                                                         'AR_RATE_PAID', 'AUTOMATED_RESOLUTIONS_PAID']].rename(columns={
                            'AR_RATE_PAID': 'AR Rate (Previous)',
                            'AUTOMATED_RESOLUTIONS_PAID': 'ARs (Previous, 28d)',
                        })
                        inst_combined = inst_latest.merge(
                            inst_prev,
                            on=['CRM_ACCOUNT_ID', 'INSTANCE_ACCOUNT_SUBDOMAIN'],
                            how='outer',
                        )
                        inst_combined = inst_combined.rename(columns={
                            'CRM_ACCOUNT_NAME': 'Account',
                            'INSTANCE_ACCOUNT_SUBDOMAIN': 'Instance',
                        })
                        # If a CRM only has the previous-row but not latest, fill the Account from the lost_df.
                        crm_to_account = dict(zip(lost_df['CRM_ACCOUNT_ID'], lost_df['Account']))
                        inst_combined['Account'] = inst_combined['Account'].fillna(
                            inst_combined['CRM_ACCOUNT_ID'].map(crm_to_account)
                        )
                        inst_combined = inst_combined.sort_values(['Account', 'Instance'])
                        inst_combined['AR Rate (Current)'] = inst_combined['AR Rate (Current)'].apply(
                            lambda x: f"{x:.1%}" if pd.notna(x) else "—"
                        )
                        inst_combined['AR Rate (Previous)'] = inst_combined['AR Rate (Previous)'].apply(
                            lambda x: f"{x:.1%}" if pd.notna(x) else "—"
                        )
                        inst_combined['ARs (Current, 28d)'] = inst_combined['ARs (Current, 28d)'].apply(
                            lambda x: f"{int(x):,}" if pd.notna(x) else "0"
                        )
                        inst_combined['ARs (Previous, 28d)'] = inst_combined['ARs (Previous, 28d)'].apply(
                            lambda x: f"{int(x):,}" if pd.notna(x) else "0"
                        )
                        st.dataframe(
                            inst_combined[['Account', 'Instance', 'AR Rate (Current)', 'AR Rate (Previous)',
                                            'ARs (Current, 28d)', 'ARs (Previous, 28d)']],
                            use_container_width=True,
                            height=400,
                            hide_index=True,
                        )

                    st.divider()

                    # Add notes input section
                    st.markdown("#### 📝 Customer Notes")
                    st.info("Select a customer below to view or edit notes explaining why they lost adoption.")

                    # Get current user
                    try:
                        session = get_active_session()
                        current_user = session.sql("SELECT CURRENT_USER()").collect()[0][0]
                    except:
                        current_user = "unknown_user"

                    # Customer selector (use renamed column names)
                    customer_names = lost_df[['CRM_ACCOUNT_ID', 'Account', 'INSTANCE_ACCOUNT_ID', 'Driving Instance']].values.tolist()
                    customer_display = [f"{name}" for id, name, inst_id, inst_name in customer_names]
                    customer_map = {f"{name}": (id, inst_id, inst_name) for id, name, inst_id, inst_name in customer_names}

                    selected_customer = st.selectbox("Select Customer", customer_display, key="lost_customer_select")

                    if selected_customer:
                        selected_id, selected_instance_id, selected_instance_name = customer_map[selected_customer]
                        existing_note = notes_dict.get(selected_id, "")

                        # Show existing note if present
                        if existing_note:
                            st.markdown("**Current Notes:**")
                            st.info(existing_note)

                        # Notes text area
                        note_text = st.text_area(
                            f"Edit notes for {selected_customer}",
                            value=existing_note,
                            height=200,
                            placeholder="Enter notes explaining why this customer lost adoption...",
                            key=f"note_input_{selected_id}"
                        )

                        if st.button("💾 Save Note", key=f"save_note_{selected_id}"):
                            with st.spinner("Saving note..."):
                                success = save_note(selected_id, selected_customer, selected_instance_id, selected_instance_name, latest_date, note_text, current_user)
                            if success:
                                st.success(f"✓ Note saved for {selected_customer} (ID: {selected_id})")
                                st.info("Refreshing page to load updated notes...")
                                st.rerun()
                            else:
                                st.error("Failed to save note - check error message above")

                    # Download button (with unformatted data for Excel, including all columns)
                    st.divider()
                    st.download_button(
                        label=":material/download: Download Lost Adoption List with Notes (CSV)",
                        data=lost_df.to_csv(index=False).encode('utf-8'),
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

    with tab_missed_targets:
        st.subheader("Missed Target Dates")
        st.caption("Customers who had a target deployment date in the last week but no bot has been deployed yet.")

        if len(gdf) == 0:
            st.warning("No data available for the selected filters.")
        else:
            # Use the latest snapshot
            gdf_sorted = gdf.copy()
            gdf_sorted['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(gdf_sorted['SOURCE_SNAPSHOT_DATE'])
            gdf_sorted = gdf_sorted.sort_values('SOURCE_SNAPSHOT_DATE')

            latest_date = gdf_sorted['SOURCE_SNAPSHOT_DATE'].max()
            one_week_ago = latest_date - timedelta(days=7)

            st.markdown(f"**As of:** {latest_date.strftime('%Y-%m-%d')}")
            st.markdown(f"**Target date window:** {one_week_ago.strftime('%Y-%m-%d')} to {latest_date.strftime('%Y-%m-%d')}")

            latest_snapshot = gdf_sorted[gdf_sorted['SOURCE_SNAPSHOT_DATE'] == latest_date].copy()

            # Convert target date columns to datetime
            if 'PROJECTED_GO_LIVE_DATE' in latest_snapshot.columns:
                latest_snapshot['PROJECTED_GO_LIVE_DATE'] = pd.to_datetime(latest_snapshot['PROJECTED_GO_LIVE_DATE'], errors='coerce')
            else:
                latest_snapshot['PROJECTED_GO_LIVE_DATE'] = pd.NaT

            if 'TARGET_DEPLOY_DATE_LAST_WEEK' in latest_snapshot.columns:
                latest_snapshot['TARGET_DEPLOY_DATE_LAST_WEEK'] = pd.to_datetime(latest_snapshot['TARGET_DEPLOY_DATE_LAST_WEEK'], errors='coerce')
            else:
                latest_snapshot['TARGET_DEPLOY_DATE_LAST_WEEK'] = pd.NaT

            # Convert bot deployed date to datetime
            if 'FIRST_BOT_DEPLOYED_DATE_PAID' in latest_snapshot.columns:
                latest_snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'] = pd.to_datetime(latest_snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'], errors='coerce')
            else:
                latest_snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'] = pd.NaT

            # Find instances with target date in the last week but no bot deployed
            # Use PROJECTED_GO_LIVE_DATE as primary, fall back to TARGET_DEPLOY_DATE_LAST_WEEK
            latest_snapshot['TARGET_DATE'] = latest_snapshot['PROJECTED_GO_LIVE_DATE'].fillna(latest_snapshot['TARGET_DEPLOY_DATE_LAST_WEEK'])

            missed_targets_filter = (
                (latest_snapshot['TARGET_DATE'] > one_week_ago) &
                (latest_snapshot['TARGET_DATE'] <= latest_date) &
                (latest_snapshot['FIRST_BOT_DEPLOYED_DATE_PAID'].isna())
            )

            missed_targets_df = latest_snapshot[missed_targets_filter].copy()

            # Display count
            st.metric("Instances with Missed Targets", len(missed_targets_df))

            if len(missed_targets_df) > 0:
                st.divider()
                st.markdown("### 📋 Customers with Missed Target Dates")

                # Aggregate by CRM account
                missed_agg = missed_targets_df.groupby('CRM_ACCOUNT_ID').agg({
                    'CRM_ACCOUNT_NAME': 'first',
                    'INSTANCE_ACCOUNT_ID': 'first',
                    'INSTANCE_ACCOUNT_SUBDOMAIN': 'first',
                    'CRM_REGION': 'first',
                    'CRM_ARR_BAND_BROAD': 'first',
                    'CRM_MARKET_SEGMENT': 'first',
                    'AI_STRATEGIST_NAME': 'first',
                    'CONSULTANT_NAME': 'first',
                    'TARGET_DATE': 'first',
                    'PROJECTED_GO_LIVE_DATE': 'first',
                    'CURRENT_PHASE': 'first',
                    'COHORT': 'first'
                }).reset_index()

                # Rename columns for display
                missed_display = missed_agg.rename(columns={
                    'CRM_ACCOUNT_NAME': 'Account',
                    'INSTANCE_ACCOUNT_SUBDOMAIN': 'Instance',
                    'CRM_REGION': 'Region',
                    'CRM_ARR_BAND_BROAD': 'ARR Band',
                    'CRM_MARKET_SEGMENT': 'Segment',
                    'AI_STRATEGIST_NAME': 'AI Strategist',
                    'CONSULTANT_NAME': 'CSM',
                    'TARGET_DATE': 'Target Date',
                    'PROJECTED_GO_LIVE_DATE': 'Projected Go-Live',
                    'CURRENT_PHASE': 'Current Phase',
                    'COHORT': 'Cohort'
                })

                # Format dates for display
                for date_col in ['Target Date', 'Projected Go-Live']:
                    if date_col in missed_display.columns:
                        missed_display[date_col] = pd.to_datetime(missed_display[date_col], errors='coerce').dt.strftime('%Y-%m-%d')

                # Calculate days overdue
                missed_display['Days Overdue'] = (latest_date - pd.to_datetime(missed_agg['TARGET_DATE'])).dt.days

                # Sort by days overdue (descending) and account name
                missed_display = missed_display.sort_values(['Days Overdue', 'Account'], ascending=[False, True])

                # Select columns for display
                display_columns = [
                    'Account', 'Instance', 'Target Date', 'Days Overdue',
                    'Region', 'ARR Band', 'Segment', 'Current Phase', 'Cohort',
                    'AI Strategist', 'CSM'
                ]
                display_columns = [col for col in display_columns if col in missed_display.columns]

                st.dataframe(
                    missed_display[display_columns],
                    use_container_width=True,
                    height=400,
                    hide_index=True
                )

                # Summary statistics
                st.divider()
                st.markdown("### 📊 Summary Statistics")

                col1, col2, col3 = st.columns(3)
                with col1:
                    avg_overdue = missed_display['Days Overdue'].mean()
                    st.metric("Average Days Overdue", f"{avg_overdue:.1f}")
                with col2:
                    max_overdue = missed_display['Days Overdue'].max()
                    st.metric("Max Days Overdue", f"{int(max_overdue)}")
                with col3:
                    unique_crms = missed_display['CRM_ACCOUNT_ID'].nunique()
                    st.metric("Unique Accounts", unique_crms)

                # Breakdown by region if available
                if 'Region' in missed_display.columns:
                    st.divider()
                    st.markdown("### 🌍 Breakdown by Region")
                    region_breakdown = missed_display.groupby('Region').agg({
                        'CRM_ACCOUNT_ID': 'count',
                        'Days Overdue': 'mean'
                    }).reset_index()
                    region_breakdown.columns = ['Region', 'Count', 'Avg Days Overdue']
                    region_breakdown = region_breakdown.sort_values('Count', ascending=False)
                    st.dataframe(region_breakdown, use_container_width=True, hide_index=True)

                # Download button
                st.divider()
                st.download_button(
                    label=":material/download: Download Missed Targets List (CSV)",
                    data=missed_display.to_csv(index=False).encode('utf-8'),
                    file_name=f"missed_target_dates_{latest_date.strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
            else:
                st.success("✅ No customers missed their target deployment dates in the last week!")

    with tab5:
        st.subheader("Data Explorer")

        all_columns = gdf_filtered.columns.tolist()
        default_cols = ['SOURCE_SNAPSHOT_DATE', 'INSTANCE_ACCOUNT_SUBDOMAIN', 'CRM_ACCOUNT_NAME', 'CRM_REGION', 'CRM_ARR_BAND_BROAD', 'AR_RATE_PAID']
        default_cols = [col for col in default_cols if col in all_columns]

        search_fields = [c for c in ('CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME', 'INSTANCE_ACCOUNT_ID', 'INSTANCE_ACCOUNT_SUBDOMAIN') if c in all_columns]
        search_term = st.text_input(
            "Search account",
            placeholder="Enter CRM account ID/name, instance account ID, or subdomain (partial matches OK)",
            key="data_explorer_search",
        )

        explorer_df = gdf_filtered
        if search_term and search_fields:
            term_lower = search_term.strip().lower()
            mask = pd.Series(False, index=explorer_df.index)
            for field in search_fields:
                mask = mask | explorer_df[field].astype(str).str.lower().str.contains(term_lower, na=False)
            explorer_df = explorer_df[mask]
            st.caption(f"Matched {len(explorer_df):,} rows across {', '.join(search_fields)}.")

        selected_columns = st.multiselect("Select columns", all_columns, default=default_cols if default_cols else all_columns[:10])

        if selected_columns:
            st.dataframe(explorer_df[selected_columns].head(1000), use_container_width=True, height=500)

    with tab_icl:
        st.subheader("Integrated Cohort List")
        st.caption(
            "Replicates the Integrated Cohort List SQL: one row per instance for the selected snapshot date, "
            "ordered by cohort then ARR. AR / AR count / AI Agent interactions are shown as both `_paid` and `_advanced` variants."
        )

        # Snapshot date selector — defaults to the latest available
        icl_dates = sorted(gdf['SOURCE_SNAPSHOT_DATE'].dropna().unique(), reverse=True)
        if len(icl_dates) == 0:
            st.warning("No data available.")
        else:
            default_date = icl_dates[0]
            icl_date = st.selectbox(
                "Snapshot date",
                options=icl_dates,
                index=0,
                format_func=lambda d: pd.Timestamp(d).strftime('%Y-%m-%d'),
                key='icl_snapshot_date',
            )

            icl_df = gdf[gdf['SOURCE_SNAPSHOT_DATE'] == icl_date].copy()

            # Merge in AI Expert project health summary (one row per CRM, picked
            # by tiebreaker rules — see load_aie_project_health docstring).
            aie_health, aie_err = load_aie_project_health()
            if aie_err or aie_health is None:
                st.caption(f"Could not load zd_health_summary: {aie_err}")
                icl_df['__ZD_HEALTH_SUMMARY__'] = None
            else:
                aie_lookup = aie_health.rename(columns={
                    'CRM_ACCOUNT_ID': 'CRM_ACCOUNT_ID',
                    'ZD_HEALTH_SUMMARY_C': '__ZD_HEALTH_SUMMARY__',
                })[['CRM_ACCOUNT_ID', '__ZD_HEALTH_SUMMARY__']]
                icl_df = icl_df.merge(aie_lookup, on='CRM_ACCOUNT_ID', how='left')

            # Merge in manual delay_code and Q2 target overrides.
            overrides_df, overrides_err = load_integrated_cohort_overrides()
            if overrides_err or overrides_df is None:
                st.caption(f"Could not load manual cohort overrides: {overrides_err}")
                icl_df['__Q2_TARGET__'] = None
                icl_df['__DELAY_CODE__'] = None
            else:
                overrides_lookup = overrides_df.rename(columns={
                    'CRM_ACCOUNT_ID': 'CRM_ACCOUNT_ID',
                    'Q2_TARGET_ACCOUNT': '__Q2_TARGET__',
                    'DELAY_CODE': '__DELAY_CODE__',
                })[['CRM_ACCOUNT_ID', '__Q2_TARGET__', '__DELAY_CODE__']]
                icl_df = icl_df.merge(overrides_lookup, on='CRM_ACCOUNT_ID', how='left')

            # Column projection: (source_col_or_None, output_col_name)
            # NULL placeholders are used for fields not yet sourced from the mart;
            # when the mart adds them we update the source column in place.
            column_map = [
                ('SOURCE_SNAPSHOT_DATE',                    'source_snapshot_date'),
                ('INSTANCE_ACCOUNT_ID',                     'instance_account_id'),
                ('INSTANCE_ACCOUNT_SUBDOMAIN',              'instance_account_subdomain'),
                ('CRM_ACCOUNT_ID',                          'crm_account_id'),
                ('CRM_ACCOUNT_NAME',                        'crm_account_name'),
                ('CRM_NET_ARR_USD',                         'customer_net_arr_usd'),
                ('CRM_ARR_BAND_BROAD',                      'crm_arr_band'),
                ('AI_ARR',                                  'ai_arr'),
                ('CRM_MARKET_SEGMENT',                      'customer_segment'),
                ('CRM_REGION',                              'region'),
                ('CRM_SUB_REGION',                          'subregion'),
                ('CRM_INDUSTRY',                            'industry'),
                ('CRM_MARKET_SUPER_SEGMENT',                'cs_segment'),
                ('CRM_NEXT_RENEWAL_DATE',                   'renewal_date'),
                ('CRM_HEALTH_STATUS',                       'crm_health_status'),
                ('AI_EXPERT_FLAG',                          'purchased_aie'),
                ('CRM_AI_EXPERT_SKU_SUBSCRIBED_START_DATE', 'aie_project_start_date'),
                ('CONSULTANT_NAME',                         'consultant'),
                ('CONSULTANT_MANAGER',                      'consultant_manager'),
                ('SUBCO_ORGANIZATION',                      'consultant_subcontractor_name'),
                ('AI_STRATEGIST_NAME',                      'strategist'),
                ('AI_STRATEGIST_MANAGER_NAME',              'strategist_manager'),
                ('THIRD_PARTY_AI_BOT',                      'third_party_ai_agent'),
                ('THIRD_PARTY_AI_BOT_FIRST_SEEN_DATE',      'third_party_ai_agent_first_seen_date'),
                ('THIRD_PARTY_AI_BOT_LAST_SEEN_DATE',       'third_party_ai_agent_last_seen_date'),
                ('CURRENT_PHASE',                           'current_phase'),
                ('COHORT',                                  'usage_cohort_snapshot'),
                ('MONTHLY_COHORT',                          'monthly_cohort'),
                ('__Q2_TARGET__',                           'q2_target_account'),
                ('PROJECT_HEALTH',                          'project_health'),
                ('__ZD_HEALTH_SUMMARY__',                   'certinia_project_health_summary'),
                ('BOT_TYPE',                                'ai_agent_type'),
                ('PROJECTED_GO_LIVE_DATE',                  'target_deploy_date'),
                ('TARGET_DEPLOY_DATE_LAST_WEEK',            'targeted_deploy_last_week'),
                ('ACTUAL_GO_LIVE_DATE',                     'actual_deploy_date'),
                ('__DELAY_CODE__',                          'delay_codes'),
                ('ACTIVE_BOTS_28D_COUNT',                   'num_ai_agents_by_channel'),
                (None,                                      'num_use_cases'),                    # GAP
                ('AR_RATE_PAID',                            'ar_rate_28d_paid'),
                ('AR_RATE_ADVANCED',                        'ar_rate_28d_advanced'),
                ('AUTOMATED_RESOLUTIONS_PAID',              'num_automated_resolutions_28d_paid'),
                ('AUTOMATED_RESOLUTIONS_ADVANCED',          'num_automated_resolutions_28d_advanced'),
                ('BOT_INTERACTIONS_PAID',                   'num_ai_agent_interactions_28d_paid'),
                ('BOT_INTERACTIONS_ADVANCED',               'num_ai_agent_interactions_28d_advanced'),
                ('TOP_BOX_PERCENTAGE',                      'bsat_28d_top_box_pct'),
                ('CRM_IS_AI_AGENTS_PAID_ACTIVATED',         'crm_paid_activated'),
                ('CRM_IS_AI_AGENTS_PAID_ADOPTED',           'crm_paid_adopted'),
                ('CRM_IS_AI_AGENTS_ADVANCED_ACTIVATED',     'crm_advanced_activated'),
                ('CRM_IS_AI_AGENTS_ADVANCED_ADOPTED',       'crm_advanced_adopted'),
                ('INSTANCE_IS_AI_AGENTS_PAID_ACTIVATED',    'instance_paid_activated'),
                ('INSTANCE_IS_AI_AGENTS_PAID_ADOPTED',      'instance_paid_adopted'),
                ('INSTANCE_IS_AI_AGENTS_ADVANCED_ACTIVATED','instance_advanced_activated'),
                ('INSTANCE_IS_AI_AGENTS_ADVANCED_ADOPTED',  'instance_advanced_adopted'),
                ('FIRST_INSTANCE_PAID_ACTIVATED_DATE',      'first_instance_paid_activated_date'),
                ('FIRST_ADOPTION_DATE_PAID',                'first_instance_paid_adopted_date'),
                ('FIRST_INSTANCE_ADVANCED_ACTIVATED_DATE',  'first_instance_advanced_activated_date'),
                ('FIRST_ADOPTION_DATE_ADVANCED',            'first_instance_advanced_adopted_date'),
                ('FIRST_CRM_PAID_ACTIVATED_DATE',           'first_crm_paid_activated_date'),
                ('FIRST_CRM_PAID_ADOPTED_DATE',             'first_crm_paid_adopted_date'),
                ('FIRST_CRM_ADVANCED_ACTIVATED_DATE',       'first_crm_advanced_activated_date'),
                ('FIRST_CRM_ADVANCED_ADOPTED_DATE',         'first_crm_advanced_adopted_date'),
            ]

            output = pd.DataFrame(index=icl_df.index)
            missing_cols = []
            for src, out in column_map:
                if src is None:
                    output[out] = None
                elif src in icl_df.columns:
                    output[out] = icl_df[src]
                else:
                    output[out] = None
                    missing_cols.append(src)

            # Sort: cohort asc, ARR desc nulls last, instance_account_id asc
            sort_cols = ['usage_cohort_snapshot', 'customer_net_arr_usd', 'instance_account_id']
            sort_asc = [True, False, True]
            output['__arr_null'] = output['customer_net_arr_usd'].isna()
            output = output.sort_values(
                by=['usage_cohort_snapshot', '__arr_null', 'customer_net_arr_usd', 'instance_account_id'],
                ascending=[True, True, False, True],
            ).drop(columns='__arr_null')

            if missing_cols:
                st.warning(f"Columns missing from mart (filled with NULL): {', '.join(missing_cols)}")

            # Cohort definitions are pinned to the 2026-04-30 snapshot — they
            # don't refresh with the selected as-of date.
            usage_cohort_label = "Usage Cohort Snapshot (2026-04-30)"

            def _sort_monthly_cohort(values):
                # "Apr 2026", "May 2026" -> chronological order. Unparseable
                # values (e.g. None) sort last alphabetically.
                def key(v):
                    try:
                        return (0, pd.to_datetime(v, format='%b %Y'))
                    except (ValueError, TypeError):
                        return (1, str(v))
                return sorted(values, key=key)

            with st.expander(":material/filter_alt: Filters", expanded=False):
                def _multi_options(col):
                    if col not in output.columns:
                        return []
                    return sorted([v for v in output[col].dropna().unique().tolist()])

                f1, f2, f3 = st.columns(3)
                with f1:
                    sel_cohort = st.multiselect(usage_cohort_label, _multi_options('usage_cohort_snapshot'), key='icl_f_cohort')
                    sel_monthly = st.multiselect("Monthly cohort", _sort_monthly_cohort([v for v in output['monthly_cohort'].dropna().unique().tolist()]) if 'monthly_cohort' in output.columns else [], key='icl_f_monthly')
                    sel_phase = st.multiselect("Current phase", _multi_options('current_phase'), key='icl_f_phase')
                with f2:
                    sel_health = st.multiselect("Project health", _multi_options('project_health'), key='icl_f_health')
                    sel_consultant = st.multiselect("Consultant", _multi_options('consultant'), key='icl_f_consultant')
                    sel_strategist = st.multiselect("Strategist", _multi_options('strategist'), key='icl_f_strategist')
                with f3:
                    sel_strat_mgr = st.multiselect("Strategist manager", _multi_options('strategist_manager'), key='icl_f_strat_mgr')
                    sel_q2 = st.radio("Q2 Target Account", ['All', 'Yes', 'No / blank'], horizontal=True, key='icl_f_q2')
                    sel_inst_paid_adp = st.radio("Instance — AI Agents Paid adopted", ['All', 'True', 'False'], horizontal=True, key='icl_f_inst_paid_adp')
                    sel_inst_adv_adp = st.radio("Instance — AI Agents Advanced adopted", ['All', 'True', 'False'], horizontal=True, key='icl_f_inst_adv_adp')
                    sel_crm_paid_adp = st.radio("CRM — AI Agents Paid adopted", ['All', 'True', 'False'], horizontal=True, key='icl_f_crm_paid_adp')
                    sel_crm_adv_adp = st.radio("CRM — AI Agents Advanced adopted", ['All', 'True', 'False'], horizontal=True, key='icl_f_crm_adv_adp')

            def _apply_multi(df, col, selected):
                if not selected or col not in df.columns:
                    return df
                return df[df[col].isin(selected)]

            def _apply_bool_radio(df, col, choice):
                if choice == 'All' or col not in df.columns:
                    return df
                want = choice == 'True'
                return df[df[col] == want]

            output = _apply_multi(output, 'usage_cohort_snapshot', sel_cohort)
            output = _apply_multi(output, 'monthly_cohort', sel_monthly)
            output = _apply_multi(output, 'current_phase', sel_phase)
            output = _apply_multi(output, 'project_health', sel_health)
            output = _apply_multi(output, 'consultant', sel_consultant)
            output = _apply_multi(output, 'strategist', sel_strategist)
            output = _apply_multi(output, 'strategist_manager', sel_strat_mgr)

            if sel_q2 == 'Yes' and 'q2_target_account' in output.columns:
                output = output[output['q2_target_account'] == 'Yes']
            elif sel_q2 == 'No / blank' and 'q2_target_account' in output.columns:
                output = output[output['q2_target_account'] != 'Yes']

            output = _apply_bool_radio(output, 'instance_paid_adopted', sel_inst_paid_adp)
            output = _apply_bool_radio(output, 'instance_advanced_adopted', sel_inst_adv_adp)
            output = _apply_bool_radio(output, 'crm_paid_adopted', sel_crm_paid_adp)
            output = _apply_bool_radio(output, 'crm_advanced_adopted', sel_crm_adv_adp)

            # Rename the column for display now that filtering has finished.
            if 'usage_cohort_snapshot' in output.columns:
                output = output.rename(columns={'usage_cohort_snapshot': usage_cohort_label})

            icl_search_fields = [c for c in ('crm_account_id', 'crm_account_name', 'instance_account_id', 'instance_account_subdomain') if c in output.columns]
            icl_search_term = st.text_input(
                "Search account",
                placeholder="Enter CRM account ID/name, instance account ID, or subdomain (partial matches OK)",
                key="icl_search",
            )
            if icl_search_term and icl_search_fields:
                term_lower = icl_search_term.strip().lower()
                mask = pd.Series(False, index=output.index)
                for field in icl_search_fields:
                    mask = mask | output[field].astype(str).str.lower().str.contains(term_lower, na=False)
                output = output[mask]
                st.caption(f"Matched {len(output):,} rows across {', '.join(icl_search_fields)}.")

            # Merge in shared notes for this snapshot date.
            notes_df, notes_err = load_icl_notes(icl_date)
            if notes_err:
                st.caption(f"Could not load notes: {notes_err}")
                output['notes'] = ''
                output['last_edited'] = ''
            else:
                if notes_df is None or len(notes_df) == 0:
                    notes_lookup = pd.DataFrame(columns=['INSTANCE_ACCOUNT_ID', 'NOTE', 'UPDATED_BY', 'UPDATED_AT'])
                else:
                    notes_lookup = notes_df.copy()
                notes_lookup['__last_edited__'] = notes_lookup.apply(
                    lambda r: f"{r['UPDATED_BY']} · {pd.Timestamp(r['UPDATED_AT']).strftime('%Y-%m-%d %H:%M')} UTC"
                    if pd.notna(r.get('UPDATED_AT')) else '',
                    axis=1,
                )
                merged_notes = notes_lookup.rename(columns={
                    'INSTANCE_ACCOUNT_ID': 'instance_account_id',
                    'NOTE': 'notes',
                    '__last_edited__': 'last_edited',
                })[['instance_account_id', 'notes', 'last_edited']]
                # Force matching dtypes — Snowflake may return int while the
                # mart's instance_account_id is a string.
                merged_notes['instance_account_id'] = merged_notes['instance_account_id'].astype(str)
                output['instance_account_id'] = output['instance_account_id'].astype(str)
                output = output.merge(merged_notes, on='instance_account_id', how='left')
                output['notes'] = output['notes'].fillna('')
                output['last_edited'] = output['last_edited'].fillna('')

            # Format source_snapshot_date as date-only string for display.
            if 'source_snapshot_date' in output.columns:
                output['source_snapshot_date'] = pd.to_datetime(
                    output['source_snapshot_date'], errors='coerce'
                ).dt.strftime('%Y-%m-%d')

            # Reorder so notes/last_edited sit near the start of the table for
            # easier note-taking against the row's identifying fields.
            front_cols = ['source_snapshot_date', 'instance_account_id', 'instance_account_subdomain', 'crm_account_id', 'crm_account_name', 'notes', 'last_edited']
            front_cols = [c for c in front_cols if c in output.columns]
            other_cols = [c for c in output.columns if c not in front_cols]
            output = output[front_cols + other_cols]

            st.markdown(f"**Rows:** {len(output):,}")
            st.caption("Edit the **notes** column inline — changes save to a shared table and are visible to other users.")

            try:
                current_user = get_active_session().sql("SELECT CURRENT_USER()").collect()[0][0]
            except Exception:
                current_user = 'unknown_user'

            # Build column_config — notes editable, last_edited read-only,
            # everything else read-only too (data_editor edits everything by default).
            column_config = {
                'notes': st.column_config.TextColumn(
                    'notes',
                    help='Click a cell to add or edit a shared note for this row.',
                    width='medium',
                ),
                'last_edited': st.column_config.TextColumn(
                    'last_edited',
                    help='Most recent author and edit time.',
                    disabled=True,
                ),
            }
            for col in output.columns:
                if col not in ('notes',):
                    column_config.setdefault(col, st.column_config.Column(disabled=True))

            edited = st.data_editor(
                output,
                use_container_width=True,
                height=600,
                column_config=column_config,
                hide_index=True,
                key=f"icl_editor_{pd.Timestamp(icl_date).strftime('%Y%m%d')}",
            )

            # Detect note changes and persist them.
            try:
                before = output.set_index('instance_account_id')['notes'].fillna('')
                after = edited.set_index('instance_account_id')['notes'].fillna('')
                changed_ids = before.index[before.values != after.reindex(before.index).values]
                for iac in changed_ids:
                    new_text = after.loc[iac]
                    ok, err = upsert_icl_note(str(iac), icl_date, str(new_text), current_user)
                    if not ok:
                        st.error(f"Failed to save note for instance {iac}: {err}")
                if len(changed_ids) > 0:
                    load_icl_notes.clear() if hasattr(load_icl_notes, 'clear') else None
                    st.success(f"Saved {len(changed_ids)} note update(s).")
            except Exception as e:
                st.warning(f"Note persistence failed: {e}")

            st.download_button(
                label=":material/download: Download Integrated Cohort List (CSV)",
                data=output.to_csv(index=False).encode('utf-8'),
                file_name=f"integrated_cohort_list_{pd.Timestamp(icl_date).strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

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

    with tab7:
        st.subheader(":material/rocket_launch: Kickoff Call Impact on Time-to-Value")
        st.caption("Does earlier kickoff timing correlate with faster AI Agent deployment and adoption?")

        # Load kickoff analysis data
        @st.cache_data(ttl=3600)
        def load_kickoff_analysis():
            """Load kickoff call data and join with time-to-value metrics"""
            try:
                session = get_active_session()

                query = """
                WITH kickoff_calls AS (
                    SELECT
                        aiaa.CRM_ACCOUNT_ID,
                        aiaa.CRM_ACCOUNT_NAME,
                        aiaa.INSTANCE_ACCOUNT_ID,
                        aiaa.AIAA_START_DATE,
                        aiaa.AI_EXPERT_FLAG,
                        aiaa.CRM_REGION,
                        aiaa.CRM_MARKET_SEGMENT,
                        gc.call_date AS KICKOFF_CALL_DATE,
                        DATEDIFF(day, aiaa.AIAA_START_DATE, gc.call_date) AS DAYS_TO_KICKOFF,
                        gc.title AS CALL_TITLE,
                        CASE
                            WHEN LOWER(gc.title) LIKE '%kickoff%' THEN 100
                            WHEN LOWER(gc.title) LIKE '%kick off%' THEN 100
                            WHEN LOWER(gc.title) LIKE '%kick-off%' THEN 100
                            WHEN LOWER(gc.title) LIKE '%onboarding%' THEN 90
                            WHEN LOWER(gc.title) LIKE '%implementation%' THEN 85
                            WHEN LOWER(gc.title) LIKE '%project start%' THEN 85
                            WHEN LOWER(gc.title) LIKE '%getting started%' THEN 80
                            ELSE 0
                        END AS title_score,
                        ROW_NUMBER() OVER (
                            PARTITION BY aiaa.CRM_ACCOUNT_ID
                            ORDER BY
                                CASE
                                    WHEN LOWER(gc.title) LIKE '%kickoff%' THEN 100
                                    WHEN LOWER(gc.title) LIKE '%kick off%' THEN 100
                                    WHEN LOWER(gc.title) LIKE '%kick-off%' THEN 100
                                    WHEN LOWER(gc.title) LIKE '%onboarding%' THEN 90
                                    WHEN LOWER(gc.title) LIKE '%implementation%' THEN 85
                                    WHEN LOWER(gc.title) LIKE '%project start%' THEN 85
                                    WHEN LOWER(gc.title) LIKE '%getting started%' THEN 80
                                    ELSE 0
                                END DESC,
                                gc.call_date ASC,
                                DATEDIFF(day, aiaa.AIAA_START_DATE, gc.call_date) ASC
                        ) AS kickoff_rank
                    FROM (
                        SELECT DISTINCT
                            CRM_ACCOUNT_ID,
                            CRM_ACCOUNT_NAME,
                            INSTANCE_ACCOUNT_ID,
                            AIAA_START_DATE,
                            AI_EXPERT_FLAG,
                            CRM_REGION,
                            CRM_MARKET_SEGMENT
                        FROM PRESENTATION.SUCCESS.AI_AGENTS_ADVANCED_COMMAND_CENTRAL
                        WHERE INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED = 'TRUE'
                            AND AIAA_START_DATE IS NOT NULL
                            AND SOURCE_SNAPSHOT_DATE = (SELECT MAX(SOURCE_SNAPSHOT_DATE)
                                                        FROM PRESENTATION.SUCCESS.AI_AGENTS_ADVANCED_COMMAND_CENTRAL)
                    ) aiaa
                    INNER JOIN FUNCTIONAL.CONVERGE.TRANSFORM_GONG_ACCOUNTS_MAP ga
                        ON aiaa.CRM_ACCOUNT_ID = ga.crm_account_id
                    INNER JOIN FUNCTIONAL.CONVERGE.UNIFIED_GONG_EVENTS gc
                        ON ga.conversation_key = gc.conversation_key
                    WHERE gc.call_date >= aiaa.AIAA_START_DATE
                        AND gc.call_date <= DATEADD(day, 90, aiaa.AIAA_START_DATE)
                ),
                high_conf_kickoffs AS (
                    SELECT *
                    FROM kickoff_calls
                    WHERE kickoff_rank = 1 AND title_score >= 80
                ),
                latest_metrics AS (
                    SELECT
                        CRM_ACCOUNT_ID,
                        INSTANCE_ACCOUNT_ID,
                        TIME_TO_DEPLOY_PAID,
                        TIME_TO_ADOPT_PAID,
                        FIRST_BOT_DEPLOYED_DATE_PAID,
                        FIRST_ADOPTION_DATE_PAID,
                        INSTANCE_IS_AI_AGENTS_ADVANCED_ACTIVATED,
                        INSTANCE_IS_AI_AGENTS_ADVANCED_ADOPTED
                    FROM PRESENTATION.SUCCESS.AI_AGENTS_ADVANCED_COMMAND_CENTRAL
                    WHERE SOURCE_SNAPSHOT_DATE = (SELECT MAX(SOURCE_SNAPSHOT_DATE)
                                                   FROM PRESENTATION.SUCCESS.AI_AGENTS_ADVANCED_COMMAND_CENTRAL)
                )
                SELECT
                    k.CRM_ACCOUNT_ID,
                    k.CRM_ACCOUNT_NAME,
                    k.INSTANCE_ACCOUNT_ID,
                    k.AIAA_START_DATE,
                    k.KICKOFF_CALL_DATE,
                    k.DAYS_TO_KICKOFF,
                    k.CALL_TITLE,
                    k.AI_EXPERT_FLAG,
                    k.CRM_REGION,
                    k.CRM_MARKET_SEGMENT,
                    m.TIME_TO_DEPLOY_PAID,
                    m.TIME_TO_ADOPT_PAID,
                    m.FIRST_BOT_DEPLOYED_DATE_PAID,
                    m.FIRST_ADOPTION_DATE_PAID,
                    m.INSTANCE_IS_AI_AGENTS_ADVANCED_ACTIVATED,
                    m.INSTANCE_IS_AI_AGENTS_ADVANCED_ADOPTED,
                    CASE
                        WHEN m.FIRST_BOT_DEPLOYED_DATE_PAID IS NOT NULL
                            THEN DATEDIFF(day, k.KICKOFF_CALL_DATE, m.FIRST_BOT_DEPLOYED_DATE_PAID)
                    END AS DAYS_KICKOFF_TO_BOT,
                    CASE
                        WHEN m.FIRST_ADOPTION_DATE_PAID IS NOT NULL
                            THEN DATEDIFF(day, k.KICKOFF_CALL_DATE, m.FIRST_ADOPTION_DATE_PAID)
                    END AS DAYS_KICKOFF_TO_ADOPTION,
                    CASE
                        WHEN k.DAYS_TO_KICKOFF <= 7 THEN '1. Within 1 week'
                        WHEN k.DAYS_TO_KICKOFF <= 14 THEN '2. Within 2 weeks'
                        WHEN k.DAYS_TO_KICKOFF <= 30 THEN '3. Within 1 month'
                        WHEN k.DAYS_TO_KICKOFF <= 60 THEN '4. Within 2 months'
                        ELSE '5. After 2 months'
                    END AS KICKOFF_TIMING_BUCKET
                FROM high_conf_kickoffs k
                LEFT JOIN latest_metrics m
                    ON k.CRM_ACCOUNT_ID = m.CRM_ACCOUNT_ID
                    AND k.INSTANCE_ACCOUNT_ID = m.INSTANCE_ACCOUNT_ID
                """

                df = session.sql(query).to_pandas()
                return df, None
            except Exception as e:
                return None, str(e)

        kickoff_df, error = load_kickoff_analysis()

        if error:
            st.error(f"Error loading kickoff analysis data: {error}")
        elif kickoff_df is None or len(kickoff_df) == 0:
            st.warning("No kickoff call data available.")
        else:
            # Summary statistics by timing bucket
            st.markdown("### 📊 Impact of Kickoff Timing on Time-to-Value")

            summary_stats = kickoff_df.groupby('KICKOFF_TIMING_BUCKET').agg({
                'CRM_ACCOUNT_ID': 'count',
                'DAYS_TO_KICKOFF': ['median'],
                'TIME_TO_DEPLOY_PAID': ['median'],
                'TIME_TO_ADOPT_PAID': ['median']
            }).reset_index()

            summary_stats.columns = [
                'Kickoff Timing', 'Accounts',
                'Median Days to Kickoff',
                'Median Time to Activate',
                'Median Time to Adopt'
            ]

            # Display summary table
            st.dataframe(
                summary_stats.style.format({
                    'Accounts': '{:.0f}',
                    'Median Days to Kickoff': '{:.1f}',
                    'Median Time to Activate': '{:.0f}',
                    'Median Time to Adopt': '{:.0f}'
                }),
                use_container_width=True,
                hide_index=True
            )

            st.markdown("""
            **💡 Interpretation Guide:**
            - **Median Time to Activate**: Days from AIAA start to activation (lower is better)
            - **Median Time to Adopt**: Days from AIAA start to adoption (lower is better)
            """)

            st.divider()

            # Customer-level data table
            st.markdown("### 📋 Customer Details")

            customer_data = kickoff_df[[
                'CRM_ACCOUNT_NAME',
                'CALL_TITLE',
                'AIAA_START_DATE',
                'KICKOFF_CALL_DATE',
                'FIRST_ADOPTION_DATE_PAID',
                'DAYS_TO_KICKOFF',
                'TIME_TO_ADOPT_PAID',
                'KICKOFF_TIMING_BUCKET'
            ]].copy()

            customer_data.columns = [
                'Customer Name',
                'Call Title',
                'AIAA Start Date',
                'Kickoff Call Date',
                'Adoption Date',
                'Days to Kickoff',
                'Days to Adopt',
                'Timing Bucket'
            ]

            # Sort by kickoff timing bucket then by days to adopt
            customer_data = customer_data.sort_values(['Timing Bucket', 'Days to Adopt'])

            st.dataframe(
                customer_data,
                use_container_width=True,
                height=400,
                hide_index=True
            )

            # Download button for customer data
            st.download_button(
                label=":material/download: Download Customer Data (CSV)",
                data=customer_data.to_csv(index=False).encode('utf-8'),
                file_name=f"kickoff_customer_data_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )

    with tab_spiff:
        st.subheader(":material/emoji_events: SPIFF Leaderboard — AI Agents Paid")
        st.info(
            f"### :material/slideshow: [Competition details]"
            f"(https://docs.google.com/presentation/d/1I-IkP5n_DgsYQFvqZDSHiuaXh5JPxXB9gRykAI2lVjY/edit?usp=sharing)\n\n"
            f"**Scoring window:** {SPIFF_WINDOW_START} to {SPIFF_WINDOW_END}  \n"
            f"**Points:** {SPIFF_POINTS_DEPLOYED} per first AI Agent deployed · "
            f"{SPIFF_POINTS_ADOPTED} per first adoption · "
            f"{SPIFF_POINTS_AR50} per first 50% AR rate"
        )
        st.caption(
            "Scope: customers currently penetrated on **AI Agents Paid**. "
            "Each CRM account counts at most once per milestone — multiple instances of the same CRM don't multiply points. "
            "If a customer hits both deployed and adopted in the window, only adopted is counted."
        )
        st.caption(
            "**Note:** This leaderboard now scores against **AI Agents Paid** (Advanced + Essentials + Legacy Gen). "
            "Previously it was scored against AI Agents Advanced only, so some point totals and counts may differ from earlier snapshots."
        )

        # Build the per-instance event dataset (latest snapshot, paid penetrated only)
        spiff_source = st.session_state.global_data.copy() if st.session_state.global_data is not None else None
        if spiff_source is None or len(spiff_source) == 0:
            st.warning("No data loaded yet.")
        else:
            latest_snap = spiff_source['SOURCE_SNAPSHOT_DATE'].max()
            st.markdown(f"**Last updated:** {pd.Timestamp(latest_snap).strftime('%Y-%m-%d')}")
            spiff_latest = spiff_source[spiff_source['SOURCE_SNAPSHOT_DATE'] == latest_snap].copy()
            spiff_latest = spiff_latest[spiff_latest['INSTANCE_IS_AI_AGENTS_PAID_PENETRATED'] == True].copy()

            for col in ('FIRST_BOT_DEPLOYED_DATE_PAID', 'FIRST_ADOPTION_DATE_PAID', 'FIRST_50PCT_AR_DATE_PAID'):
                spiff_latest[col] = pd.to_datetime(spiff_latest[col], errors='coerce')

            window_start = pd.Timestamp(SPIFF_WINDOW_START)
            window_end = pd.Timestamp(SPIFF_WINDOW_END)

            in_win = lambda s: (s >= window_start) & (s <= window_end)
            spiff_latest['DEPLOYED_IN_WIN'] = in_win(spiff_latest['FIRST_BOT_DEPLOYED_DATE_PAID']).fillna(False).astype(int)
            spiff_latest['ADOPTED_IN_WIN'] = in_win(spiff_latest['FIRST_ADOPTION_DATE_PAID']).fillna(False).astype(int)
            spiff_latest['AR50_IN_WIN'] = in_win(spiff_latest['FIRST_50PCT_AR_DATE_PAID']).fillna(False).astype(int)
            # Dedup: if adopted in window, deployed doesn't count
            spiff_latest['DEPLOYED_COUNTED'] = spiff_latest.apply(
                lambda r: 0 if r['ADOPTED_IN_WIN'] == 1 else r['DEPLOYED_IN_WIN'], axis=1
            )

            # Normalize known name aliases / typos so points roll up to the
            # canonical name from the team mapping.
            for col in ('CONSULTANT_NAME', 'AI_STRATEGIST_NAME'):
                if col in spiff_latest.columns:
                    spiff_latest[col] = spiff_latest[col].replace(SPIFF_NAME_ALIASES)

            # Drop excluded names (e.g., partner / subco resources).
            for col in ('CONSULTANT_NAME', 'AI_STRATEGIST_NAME'):
                if col in spiff_latest.columns:
                    spiff_latest.loc[spiff_latest[col].isin(SPIFF_EXCLUDED_NAMES), col] = None

            # Load team mapping
            team_df, team_err = load_spiff_team_mapping()
            if team_err or team_df is None:
                st.error(f"Could not load team mapping: {team_err}")
                team_df = pd.DataFrame(columns=['FULL_NAME', 'TEAM', 'ROLE'])
            elif len(team_df) == 0 or team_df['FULL_NAME'].notna().sum() == 0:
                st.error(
                    "Team mapping table loaded but contains no usable rows. "
                    "Check `STREAMLIT_APPS.AIAA_COMMAND_CENTRAL.SPIFF_TEAM_MAPPING` "
                    "and re-run the CTAS rebuild from the stage."
                )
                team_df = pd.DataFrame(columns=['FULL_NAME', 'TEAM', 'ROLE'])

            consultant_team = dict(zip(
                team_df[team_df['ROLE'] == 'AI Consultant']['FULL_NAME'],
                team_df[team_df['ROLE'] == 'AI Consultant']['TEAM']
            ))
            strategist_team = dict(zip(
                team_df[team_df['ROLE'] == 'AI Strategist']['FULL_NAME'],
                team_df[team_df['ROLE'] == 'AI Strategist']['TEAM']
            ))

            def build_leaderboard(name_col, team_lookup):
                """Score is per (name, CRM): a CRM only counts once per
                milestone even if it has multiple qualifying instances."""
                df = spiff_latest.copy()
                df = df[df[name_col].notna() & (df[name_col] != '')]
                # Collapse to (name, crm) — any qualifying instance flips the bit.
                per_crm = df.groupby([name_col, 'CRM_ACCOUNT_ID']).agg(
                    deployed_in_win=('DEPLOYED_IN_WIN', 'max'),
                    adopted_in_win=('ADOPTED_IN_WIN', 'max'),
                    ar50_in_win=('AR50_IN_WIN', 'max'),
                ).reset_index()
                # Apply the deployed-vs-adopted dedup at CRM level.
                per_crm['deployed_counted'] = per_crm.apply(
                    lambda r: 0 if r['adopted_in_win'] == 1 else r['deployed_in_win'], axis=1
                )
                grp = per_crm.groupby(name_col).agg(
                    bots_deployed=('deployed_counted', 'sum'),
                    adopted=('adopted_in_win', 'sum'),
                    ar50=('ar50_in_win', 'sum'),
                ).reset_index()
                grp = grp.rename(columns={name_col: 'name'})
                grp['points'] = (
                    grp['bots_deployed'] * SPIFF_POINTS_DEPLOYED
                    + grp['adopted'] * SPIFF_POINTS_ADOPTED
                    + grp['ar50'] * SPIFF_POINTS_AR50
                )
                grp['team'] = grp['name'].map(team_lookup).fillna('No team assignment')
                grp = grp.sort_values('points', ascending=False).reset_index(drop=True)
                grp['rank'] = grp.index + 1
                return grp

            consultant_lb = build_leaderboard('CONSULTANT_NAME', consultant_team)
            strategist_lb = build_leaderboard('AI_STRATEGIST_NAME', strategist_team)

            def build_team_leaderboard(individual_lb):
                # Exclude "No team assignment" — those points don't go to any team
                t = individual_lb[individual_lb['team'] != 'No team assignment'].copy()
                tg = t.groupby('team').agg(
                    bots_deployed=('bots_deployed', 'sum'),
                    adopted=('adopted', 'sum'),
                    ar50=('ar50', 'sum'),
                    points=('points', 'sum'),
                    members=('name', 'nunique'),
                ).reset_index()
                tg = tg.sort_values('points', ascending=False).reset_index(drop=True)
                tg['rank'] = tg.index + 1
                return tg

            consultant_team_lb = build_team_leaderboard(consultant_lb)
            strategist_team_lb = build_team_leaderboard(strategist_lb)

            def medal(rank):
                return {1: '🥇', 2: '🥈', 3: '🥉'}.get(rank, '')

            # ==================== TOP TEAMS (full-width row) ====================
            st.markdown("### 🏅 Top Teams")
            team_col1, team_col2 = st.columns(2)
            with team_col1:
                if len(consultant_team_lb) > 0:
                    top_team = consultant_team_lb.iloc[0]
                    st.metric(
                        label=f"🏆 Top Consultant Team — **{top_team['team']}**",
                        value=f"{int(top_team['points'])} pts",
                        delta=f"{int(top_team['members'])} members · {int(top_team['bots_deployed'])} deployed · {int(top_team['adopted'])} adopted · {int(top_team['ar50'])} AR50",
                        delta_color="off",
                    )
            with team_col2:
                if len(strategist_team_lb) > 0:
                    top_team = strategist_team_lb.iloc[0]
                    st.metric(
                        label=f"🏆 Top Strategist Team — **{top_team['team']}**",
                        value=f"{int(top_team['points'])} pts",
                        delta=f"{int(top_team['members'])} members · {int(top_team['bots_deployed'])} deployed · {int(top_team['adopted'])} adopted · {int(top_team['ar50'])} AR50",
                        delta_color="off",
                    )

            st.divider()

            # ==================== INDIVIDUAL PODIUM ====================
            st.markdown("### 🏆 Top 3 Individuals")
            podium_col1, podium_col2 = st.columns(2)

            with podium_col1:
                st.markdown("#### AI Consultants")
                for _, row in consultant_lb.head(3).iterrows():
                    st.metric(
                        label=f"{medal(row['rank'])} {row['name']} ({row['team']})",
                        value=f"{int(row['points'])} pts",
                        delta=f"{int(row['bots_deployed'])} deployed · {int(row['adopted'])} adopted · {int(row['ar50'])} AR50",
                        delta_color="off",
                    )

            with podium_col2:
                st.markdown("#### AI Strategists")
                for _, row in strategist_lb.head(3).iterrows():
                    st.metric(
                        label=f"{medal(row['rank'])} {row['name']} ({row['team']})",
                        value=f"{int(row['points'])} pts",
                        delta=f"{int(row['bots_deployed'])} deployed · {int(row['adopted'])} adopted · {int(row['ar50'])} AR50",
                        delta_color="off",
                    )

            st.divider()

            # ==================== POINTS TABLES ====================
            st.markdown("### 📊 Points Tables")

            top_n = st.slider("Show top N for each role", min_value=5, max_value=50, value=20, step=5)
            tbl_col1, tbl_col2 = st.columns(2)

            display_cols = ['rank', 'name', 'team', 'bots_deployed', 'adopted', 'ar50', 'points']

            with tbl_col1:
                st.markdown(f"#### AI Consultants (Top {top_n})")
                st.dataframe(consultant_lb[display_cols].head(top_n), hide_index=True, use_container_width=True, height=400)
            with tbl_col2:
                st.markdown(f"#### AI Strategists (Top {top_n})")
                st.dataframe(strategist_lb[display_cols].head(top_n), hide_index=True, use_container_width=True, height=400)

            team_col1, team_col2 = st.columns(2)
            team_display_cols = ['rank', 'team', 'members', 'bots_deployed', 'adopted', 'ar50', 'points']
            with team_col1:
                st.markdown("#### Consultant Teams")
                st.dataframe(consultant_team_lb[team_display_cols], hide_index=True, use_container_width=True)
            with team_col2:
                st.markdown("#### Strategist Teams")
                st.dataframe(strategist_team_lb[team_display_cols], hide_index=True, use_container_width=True)

            st.divider()

            # ==================== RAW DATA ====================
            st.markdown("### 📋 Raw Data Pull")
            st.caption("One row per currently-paid-penetrated instance with the SPIFF event dates.")

            raw_cols = [
                'INSTANCE_ACCOUNT_ID', 'INSTANCE_ACCOUNT_SUBDOMAIN', 'CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME',
                'CONSULTANT_NAME', 'AI_STRATEGIST_NAME',
                'FIRST_BOT_DEPLOYED_DATE_PAID', 'FIRST_ADOPTION_DATE_PAID', 'FIRST_50PCT_AR_DATE_PAID',
                'AUTOMATED_RESOLUTIONS_PAID', 'BOT_INTERACTIONS_PAID',
            ]
            raw_cols = [c for c in raw_cols if c in spiff_latest.columns]
            raw_df = spiff_latest[raw_cols].rename(columns={
                'FIRST_BOT_DEPLOYED_DATE_PAID': 'FIRST_AI_AGENT_DEPLOYED_DATE',
                'FIRST_ADOPTION_DATE_PAID': 'FIRST_ADOPTED_DATE',
                'FIRST_50PCT_AR_DATE_PAID': 'FIRST_AR_50_DATE',
                'AUTOMATED_RESOLUTIONS_PAID': 'NUM_AUTOMATED_RESOLUTIONS_28D',
                'BOT_INTERACTIONS_PAID': 'NUM_AI_AGENT_INTERACTIONS_28D',
                'AI_STRATEGIST_NAME': 'AI_AGENTS_SPECIALIST_NAME',
            })
            for col in ('FIRST_AI_AGENT_DEPLOYED_DATE', 'FIRST_ADOPTED_DATE', 'FIRST_AR_50_DATE'):
                if col in raw_df.columns:
                    raw_df[col] = pd.to_datetime(raw_df[col], errors='coerce').dt.strftime('%Y-%m-%d')

            spiff_search = st.text_input(
                "Search account",
                placeholder="Enter CRM account name, instance subdomain, consultant, or strategist (partial OK)",
                key="spiff_search",
            )
            spiff_search_fields = [c for c in ('CRM_ACCOUNT_NAME', 'INSTANCE_ACCOUNT_SUBDOMAIN', 'CONSULTANT_NAME', 'AI_AGENTS_SPECIALIST_NAME') if c in raw_df.columns]
            if spiff_search and spiff_search_fields:
                term = spiff_search.strip().lower()
                mask = pd.Series(False, index=raw_df.index)
                for f in spiff_search_fields:
                    mask = mask | raw_df[f].astype(str).str.lower().str.contains(term, na=False)
                raw_df = raw_df[mask]

            st.markdown(f"**Rows:** {len(raw_df):,}")
            st.dataframe(raw_df, hide_index=True, use_container_width=True, height=500)

            st.download_button(
                label=":material/download: Download SPIFF Raw Data (CSV)",
                data=raw_df.to_csv(index=False).encode('utf-8'),
                file_name=f"spiff_raw_data_{pd.Timestamp(latest_snap).strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

            # ==================== TEAM ROSTERS ====================
            with st.expander(":material/groups: Team Rosters", expanded=False):
                if len(team_df) == 0:
                    st.info("Team mapping is empty.")
                else:
                    roster_col1, roster_col2 = st.columns(2)
                    with roster_col1:
                        st.markdown("#### AI Consultant Teams")
                        consultants = team_df[team_df['ROLE'] == 'AI Consultant']
                        for team_name in sorted(consultants['TEAM'].unique()):
                            members = sorted(consultants[consultants['TEAM'] == team_name]['FULL_NAME'].tolist())
                            st.markdown(f"**{team_name}** ({len(members)})")
                            st.markdown("\n".join(f"- {m}" for m in members))
                    with roster_col2:
                        st.markdown("#### AI Strategist Teams")
                        strategists = team_df[team_df['ROLE'] == 'AI Strategist']
                        for team_name in sorted(strategists['TEAM'].unique()):
                            members = sorted(strategists[strategists['TEAM'] == team_name]['FULL_NAME'].tolist())
                            st.markdown(f"**{team_name}** ({len(members)})")
                            st.markdown("\n".join(f"- {m}" for m in members))

    with tab_3pb:
        st.subheader(":material/smart_toy: New Third-Party Bot Signals")
        st.caption(
            "CRMs where a new (specific) third-party AI Agent first appeared during the most recent mart snapshot week. "
            "A CRM may show up here even if it's had a different third-party AI Agent for a long time — what's flagged is when a *new* bot is added."
        )

        if st.session_state.global_data is None or len(st.session_state.global_data) == 0:
            st.warning("No data loaded yet.")
        else:
            mart_full = st.session_state.global_data.copy()
            # Snowpark returns DATE as object/datetime.date — coerce to
            # datetime64 so the snapshot-date filter below actually matches.
            mart_full['SOURCE_SNAPSHOT_DATE'] = pd.to_datetime(mart_full['SOURCE_SNAPSHOT_DATE'])
            latest_snap = mart_full['SOURCE_SNAPSHOT_DATE'].max()
            window_start = latest_snap - pd.Timedelta(days=6)  # 7-day window inclusive
            st.markdown(
                f"**Window:** {window_start.strftime('%Y-%m-%d')} → {latest_snap.strftime('%Y-%m-%d')}  \n"
                f"**Latest mart snapshot:** {latest_snap.strftime('%Y-%m-%d')}"
            )

            tpb_df, tpb_err = load_third_party_bot_first_seen()
            if tpb_err or tpb_df is None:
                st.error(f"Could not load third-party bot data: {tpb_err}")
            else:
                # Identify NEW (CRM, bot) pairs: first_seen falls within the
                # latest mart-snapshot week.
                tpb_df['FIRST_SEEN_DATE'] = pd.to_datetime(tpb_df['FIRST_SEEN_DATE'], errors='coerce')
                tpb_df['LAST_SEEN_DATE'] = pd.to_datetime(tpb_df['LAST_SEEN_DATE'], errors='coerce')
                new_signals = tpb_df[
                    (tpb_df['FIRST_SEEN_DATE'] >= window_start)
                    & (tpb_df['FIRST_SEEN_DATE'] <= latest_snap)
                ].copy()

                if len(new_signals) == 0:
                    st.info("No new third-party AI Agent signals detected in the latest mart-snapshot week.")
                else:
                    # Pull CRM enrichment from the latest mart snapshot,
                    # restricted to currently-AIA-penetrated instances. This
                    # is the "AIA universe" — CRMs with at least one paid- or
                    # advanced-penetrated instance today.
                    latest_mart = mart_full[mart_full['SOURCE_SNAPSHOT_DATE'] == latest_snap].copy()

                    # Coerce penetration flags to bool — Snowpark can return
                    # them as bool, int 1/0, or string 'True'/'False' depending
                    # on driver path, which made `== True` silently match nothing.
                    def _truthy(s):
                        return s.astype(str).str.lower().isin(['true', '1', 't'])
                    paid_pen_flag = _truthy(latest_mart['INSTANCE_IS_AI_AGENTS_PAID_PENETRATED'])
                    adv_pen_flag = _truthy(latest_mart['INSTANCE_IS_AI_AGENTS_ADVANCED_PENETRATED'])
                    latest_mart = latest_mart[paid_pen_flag | adv_pen_flag].copy()
                    enrichment_cols = [
                        'CRM_ACCOUNT_ID', 'CRM_ACCOUNT_NAME', 'INSTANCE_ACCOUNT_SUBDOMAIN',
                        'CRM_NET_ARR_USD', 'CRM_ARR_BAND_BROAD', 'CRM_REGION', 'CRM_MARKET_SEGMENT',
                        'CONSULTANT_NAME', 'AI_STRATEGIST_NAME',
                        'CRM_IS_AI_AGENTS_PAID_ADOPTED',
                        'CRM_IS_AI_AGENTS_PAID_ACTIVATED',
                        'CRM_IS_AI_AGENTS_PAID_PENETRATED',
                    ]
                    enrichment_cols = [c for c in enrichment_cols if c in latest_mart.columns]
                    # Collapse to one row per CRM (subdomain becomes a comma-separated list)
                    crm_enrichment = latest_mart[enrichment_cols].groupby('CRM_ACCOUNT_ID', as_index=False).agg(
                        lambda s: ', '.join(sorted(set(str(x) for x in s.dropna()))) if s.name == 'INSTANCE_ACCOUNT_SUBDOMAIN' else s.iloc[0]
                    )

                    # Force matching dtypes for merge.
                    # Inner join so we ONLY surface CRMs that are in the AIA
                    # universe (i.e., paid- or advanced-penetrated). CRMs with
                    # 3PB signal but no AIA penetration get filtered out.
                    new_signals['CRM_ACCOUNT_ID'] = new_signals['CRM_ACCOUNT_ID'].astype(str).str.strip()
                    crm_enrichment['CRM_ACCOUNT_ID'] = crm_enrichment['CRM_ACCOUNT_ID'].astype(str).str.strip()
                    merged = new_signals.merge(crm_enrichment, on='CRM_ACCOUNT_ID', how='inner')

                    # Derive a current AIA adoption status for each CRM, using
                    # the Paid SKU progression (highest stage wins).
                    def _flag_truthy(v):
                        return v is True or str(v).lower() in ('true', '1', 't')
                    def aia_status(row):
                        if _flag_truthy(row.get('CRM_IS_AI_AGENTS_PAID_ADOPTED')):
                            return 'Paid adopted'
                        if _flag_truthy(row.get('CRM_IS_AI_AGENTS_PAID_ACTIVATED')):
                            return 'Paid activated'
                        if _flag_truthy(row.get('CRM_IS_AI_AGENTS_PAID_PENETRATED')):
                            return 'Paid penetrated'
                        return 'Not penetrated'

                    if 'CRM_IS_AI_AGENTS_PAID_PENETRATED' in merged.columns:
                        merged['AIA Adoption Status'] = merged.apply(aia_status, axis=1)
                    else:
                        merged['AIA Adoption Status'] = 'unknown'

                    merged['First Seen'] = merged['FIRST_SEEN_DATE'].dt.strftime('%Y-%m-%d')

                    display_df = merged.rename(columns={
                        'CRM_ACCOUNT_ID': 'CRM Account ID',
                        'CRM_ACCOUNT_NAME': 'Account',
                        'INSTANCE_ACCOUNT_SUBDOMAIN': 'Instance Subdomain(s)',
                        'THIRD_PARTY_AI_BOT': 'New 3PB',
                        'CRM_NET_ARR_USD': 'CRM Net ARR (USD)',
                        'CRM_ARR_BAND_BROAD': 'ARR Band',
                        'CRM_REGION': 'Region',
                        'CRM_MARKET_SEGMENT': 'Segment',
                        'CONSULTANT_NAME': 'Consultant',
                        'AI_STRATEGIST_NAME': 'Strategist',
                    })
                    display_cols = [
                        'Account', 'CRM Account ID', 'Instance Subdomain(s)', 'New 3PB', 'First Seen',
                        'AIA Adoption Status',
                        'CRM Net ARR (USD)', 'ARR Band', 'Region', 'Segment',
                        'Consultant', 'Strategist',
                    ]
                    display_cols = [c for c in display_cols if c in display_df.columns]
                    display_df = display_df[display_cols].sort_values(['First Seen', 'Account'], ascending=[False, True])

                    st.markdown(f"**Rows:** {len(display_df):,}  ·  **Distinct CRMs:** {display_df['CRM Account ID'].nunique():,}")
                    st.dataframe(display_df, hide_index=True, use_container_width=True, height=500)

                    st.download_button(
                        label=":material/download: Download New 3PB Signals (CSV)",
                        data=display_df.to_csv(index=False).encode('utf-8'),
                        file_name=f"new_3pb_signals_{latest_snap.strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                    )

# Footer
st.divider()
st.markdown("<div style='text-align: center; color: gray;'>AIAA Command Central</div>", unsafe_allow_html=True)
