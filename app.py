import os
import requests
import pandas as pd
import streamlit as st
import altair as alt

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Chicago Crime Observatory",
    layout="wide",
)

API_URL = "https://data.cityofchicago.org/resource/ijzp-q8t2.json"
alt.data_transformers.disable_max_rows()

# -------------------------------------------------------------------
# Helper: resident-friendly categories
# -------------------------------------------------------------------
def categorize_for_resident(crime_type: str) -> str:
    if not isinstance(crime_type, str):
        return "Other / Uncategorized"

    c = crime_type.upper()

    property_keywords = [
        "THEFT", "BURGLARY", "ROBBERY", "MOTOR VEHICLE THEFT",
        "CRIMINAL DAMAGE", "DECEPTIVE PRACTICE", "ARSON",
    ]
    violent_keywords = [
        "BATTERY", "ASSAULT", "HOMICIDE", "KIDNAPPING",
        "CRIM SEXUAL ASSAULT", "SEX OFFENSE",
    ]
    public_safety_keywords = [
        "PUBLIC PEACE VIOLATION", "INTERFERENCE WITH PUBLIC OFFICER",
        "WEAPONS VIOLATION", "HUMAN TRAFFICKING", "PROSTITUTION",
        "GAMBLING", "NARCOTICS", "OTHER NARCOTIC VIOLATION",
        "LIQUOR LAW VIOLATION", "OBSCENITY",
    ]

    if any(k in c for k in property_keywords):
        return "Property Crime"
    if any(k in c for k in violent_keywords):
        return "Violent Crime"
    if any(k in c for k in public_safety_keywords):
        return "Public Safety / Nuisance"
    return "Other / Uncategorized"

# -------------------------------------------------------------------
# Data loading (Optimized)
# -------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data(limit: int = 300_000) -> pd.DataFrame:
    """
    Pulls crime data from the Chicago Socrata API (last 365 days).
    Uses server-side filtering ($where) to fetch only relevant records.
    """
    # Calculate date for server-side filter
    one_year_ago = pd.Timestamp.now() - pd.DateOffset(years=1)
    one_year_ago_str = one_year_ago.strftime("%Y-%m-%dT%H:%M:%S")

    params = {
        "$limit": limit,
        "$order": "date DESC",
        "$where": f"date >= '{one_year_ago_str}'"
    }

    token = os.getenv("CHICAGO_APP_TOKEN")
    headers = {"X-App-Token": token} if token else {}

    try:
        resp = requests.get(API_URL, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        raw = resp.json()
        
        df = pd.json_normalize(raw)
        
        if df.empty:
            return df

        # Date/time processing
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        
        df["date_only"] = df["date"].dt.date
        df["hour"] = df["date"].dt.hour
        df["weekday"] = df["date"].dt.day_name()
        
        # Column cleanup
        df.rename(columns={"primary_type": "primary_description"}, inplace=True)
        
        # Explicit type conversion for safety
        df["latitude"] = pd.to_numeric(df.get("latitude"), errors="coerce")
        df["longitude"] = pd.to_numeric(df.get("longitude"), errors="coerce")
        
        # Fix: Convert to Int64 (nullable int) to handle "25" vs "25.0" mismatch with GeoJSON
        df["community_area"] = pd.to_numeric(df.get("community_area"), errors="coerce").astype("Int64")
        
        # Resident-friendly buckets
        df["resident_category"] = df["primary_description"].apply(categorize_for_resident)

        return df

    except Exception as e:
        st.error(f"Failed to load data: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_chicago_neighborhoods():
    """Fetch and cache the GeoJSON data"""
    GEOJSON_URL = "https://data.cityofchicago.org/resource/igwz-8jzy.geojson"
    return alt.Data(
        url=GEOJSON_URL,
        format=alt.DataFormat(property='features', type='json')
    )

# -------------------------------------------------------------------
# Sidebar controls
# -------------------------------------------------------------------
st.sidebar.title("Chicago Crime Observatory")

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.sidebar.success("Cache cleared – data will be reloaded.")

with st.spinner("Loading crime data from Chicago API…"):
    df = load_data()

if df.empty:
    st.warning("No data loaded. Please check the API connection.")
    st.stop()

# Date range selection
min_date = df["date_only"].min()
max_date = df["date_only"].max()

if "start_date" not in st.session_state:
    st.session_state.start_date = min_date
if "end_date" not in st.session_state:
    st.session_state.end_date = max_date

date_range = st.sidebar.date_input(
    "Date range",
    value=(st.session_state.start_date, st.session_state.end_date),
    min_value=min_date,
    max_value=max_date,
)
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    st.session_state.start_date, st.session_state.end_date = date_range

start_date = st.session_state.start_date
end_date = st.session_state.end_date

# Crime Type filter
all_cats = sorted(df["primary_description"].dropna().unique())
selected_cats = st.sidebar.multiselect("Crime Types", options=all_cats, default=[])

# Domestic filter
domestic_filter = st.sidebar.selectbox(
    "Domestic incidents filter",
    options=["All incidents", "Domestic only", "Non-domestic only"],
    index=0,
)

# -------------------------------------------------------------------
# Filter Logic
# -------------------------------------------------------------------
mask = (df["date_only"] >= start_date) & (df["date_only"] <= end_date)

if domestic_filter == "Domestic only":
    mask &= df["domestic"] == True
elif domestic_filter == "Non-domestic only":
    mask &= df["domestic"] == False

if selected_cats:
    mask &= df["primary_description"].isin(selected_cats)

filtered_df = df.loc[mask].copy()

# -------------------------------------------------------------------
# Main Content
# -------------------------------------------------------------------
st.title("Chicago Crime Observatory")

st.markdown("""
### About the Chicago Crime Observatory
Welcome to the Chicago Crime Observatory. This dashboard is designed for residents, researchers, and potential movers who want to answer the question: **"When and where does crime happen in Chicago?"**

Unlike static annual reports, this tool offers a dynamic window into the city's safety trends. We analyze the last 365 days of reported incidents directly from the Chicago Data Portal. By filtering out administrative noise and focusing on primary crime categories, we aim to reveal the underlying "rhythm" of the city—from seasonal spikes in activity to the specific hourly footprints of different offenses.
""")

st.markdown(f"**Rows after filters:** {len(filtered_df):,} (out of {len(df):,} total incidents)")
st.divider()

# -------------------------------------------------------------------
# 1. Daily Trend
# -------------------------------------------------------------------
st.subheader("1. The Daily Rhythm: Seasonal & Weekly Patterns")
st.markdown("""
Crime is rarely random; it follows distinct temporal patterns. The timeline below tracks the total volume of reported incidents over the past year.

Because daily data can be "noisy"—spiking erratically due to random events—we use a **7-Day Rolling Average** (the red line) to smooth out these fluctuations. This reveals the true trend. Look for the "Summer Spike," a common phenomenon in Chicago where incident reports rise with the temperature, and notice how activity often dips during major winter holidays.
""")

daily = filtered_df.groupby("date_only").size().reset_index(name="incidents").sort_values("date_only")
daily["rolling_incidents"] = daily["incidents"].rolling(window=7, min_periods=1).mean()

if not daily.empty:
    daily_chart = (
        alt.Chart(daily)
        .mark_line(color="#d62728")
        .encode(
            x=alt.X("date_only:T", title="Date"),
            y=alt.Y("rolling_incidents:Q", title="7-Day Rolling Average"),
            tooltip=["date_only:T", "incidents:Q", "rolling_incidents:Q"]
        )
        .properties(height=300)
    )
    st.altair_chart(daily_chart, use_container_width=True)
else:
    st.info("No data available for the timeline.")

st.divider()

# -------------------------------------------------------------------
# 2. Interactive Dashboard
# -------------------------------------------------------------------
st.subheader("2. Interactive Analysis: Correlating Crime Types with Locations")
st.markdown("""
Safety varies significantly by neighborhood and by the type of offense. This interactive section lets you drill down into those specifics.

**How to use this:** The Bar Chart (left) and the Map (right) are linked. **Click on any bar**—for example, "MOTOR VEHICLE THEFT"—to instantly filter the map. This interaction reveals hidden spatial truths: you might find that while Battery incidents are widespread, Theft is often concentrated in high-traffic commercial districts. Unselected bars will fade to gray, indicating that the map is currently focused on your selection.
""")

# Prepare Interaction Data
# Groupings must preserve numeric integrity but be serializable
chart_data = filtered_df.groupby(["primary_description", "community_area"]).size().reset_index(name="count")

# Ensure types for Altair
chart_data["count"] = chart_data["count"].astype(int)
# Convert Int64 community_area to string for lookup keys (removing .0)
chart_data["community_area"] = chart_data["community_area"].astype(str)

if not chart_data.empty:
    # Selection Signal
    selection = alt.selection_point(fields=["primary_description"], on="click")

    # Bar Chart (Left)
    bar_chart = (
        alt.Chart(chart_data)
        .transform_aggregate(
            total_count="sum(count)",
            groupby=["primary_description"]
        )
        .transform_window(
            rank="rank(total_count)",
            sort=[alt.SortField("total_count", order="descending")]
        )
        .transform_filter(alt.datum.rank <= 20)
        .mark_bar()
        .encode(
            x=alt.X("total_count:Q", title="Incidents"),
            y=alt.Y("primary_description:N", sort="-x", title="Crime Type"),
            color=alt.condition(
                selection,
                alt.value("#1f77b4"),  # Selected: Blue
                alt.value("lightgray") # Unselected: Gray
            ),
            tooltip=["primary_description:N", "total_count:Q"]
        )
        .add_params(selection)
        .properties(title="Top Crime Types (Click to Filter Map)", width="container", height=400)
    )

    # Map Chart (Right)
    chicago_neighborhoods = get_chicago_neighborhoods()
    
    map_chart = (
        alt.Chart(chart_data)
        .transform_filter(selection)
        .transform_aggregate(
            crime_count="sum(count)",
            groupby=["community_area"]
        )
        .transform_lookup(
            lookup="community_area",
            from_=alt.LookupData(
                data=chicago_neighborhoods,
                key="properties.area_num_1",
                fields=["type", "geometry", "properties"]
            )
        )
        .transform_calculate(
            crime_count_filled="isValid(datum.crime_count) ? datum.crime_count : 0"
        )
        .mark_geoshape(stroke="white", strokeWidth=0.5)
        .encode(
            color=alt.Color(
                "crime_count_filled:Q",
                title="Counts",
                scale=alt.Scale(scheme="reds")
            ),
            tooltip=[
                alt.Tooltip("properties.community:N", title="Community"),
                alt.Tooltip("crime_count_filled:Q", title="Count"),
            ]
        )
        .project(type="mercator")
        .properties(title="Geospatial Distribution", width="container", height=400)
    )

    # Use hconcat for Linked Views side-by-side layout
    combined_dashboard = alt.hconcat(bar_chart, map_chart).resolve_legend(color="independent")
    
    st.altair_chart(combined_dashboard, use_container_width=True)

else:
    st.info("No data available for the dashboard.")

st.divider()

# -------------------------------------------------------------------
# 3. Heatmap
# -------------------------------------------------------------------
st.subheader("3. The 'Risk Clock': Weekday vs. Hour Analysis")
st.markdown("""
Beyond where crime happens, it is crucial to understand when it happens. This heatmap aggregates thousands of incidents to visualize the city's "Risk Clock."

The vertical axis represents the **Day of the Week**, while the horizontal axis tracks the **Hour of the Day** (0–23). Darker orange zones indicate high-intensity windows. You will often see distinct "signatures" here: the morning rush hour may bring a wave of property crimes, while late nights on weekends often see a rise in public safety incidents. Use this to understand the typical weekly schedule of safety in Chicago.
""")

hourly = filtered_df.groupby(["weekday", "hour"]).size().reset_index(name="count")
weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

if not hourly.empty:
    heatmap = (
        alt.Chart(hourly)
        .mark_rect()
        .encode(
            x=alt.X("hour:O", title="Hour (0-23)"),
            y=alt.Y("weekday:N", title="Day", sort=weekday_order),
            color=alt.Color("count:Q", title="Incidents", scale=alt.Scale(scheme="oranges")),
            tooltip=["weekday:N", "hour:O", "count:Q"]
        )
        .properties(height=350)
    )
    st.altair_chart(heatmap, use_container_width=True)
else:
    st.info("No data available for heatmap.")

st.divider()
st.link_button("Source: Chicago Data Portal", "https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2/about_data", type="primary")