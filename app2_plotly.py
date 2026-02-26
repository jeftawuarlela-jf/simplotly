"""
Supply Chain Simulation – Streamlit Web App (Plotly Edition)
Run with:  streamlit run app2_plotly.py
"""

import streamlit as st
import streamlit.components.v1 as components
import subprocess
import sys
import os
import io
import zipfile
import tempfile
import textwrap
import glob
import pandas as pd
from datetime import date, datetime

# ─────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Supply Chain Simulation",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #1e2a38; }
    [data-testid="stSidebar"] * { color: #e0e6ef !important; }
    [data-testid="stSidebar"] .stMarkdown h3 { color: #7eb8f7 !important; font-size: 0.85rem;
        text-transform: uppercase; letter-spacing: 1px; margin-top: 1.2rem; }
    .run-box { background: #1a3a2b; border-left: 4px solid #2980b9;
        padding: 1rem; border-radius: 6px; margin-bottom: 1rem; }
    .log-box { background: #0d1117; color: #c9d1d9; font-family: monospace;
        font-size: 0.78rem; padding: 1rem; border-radius: 6px;
        max-height: 400px; overflow-y: auto; white-space: pre-wrap; }
    div[data-testid="metric-container"] { background: #f7f9fc;
        border: 1px solid #e0e6ef; border-radius: 8px; padding: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/supply-chain.png", width=60)
    st.title("Simulation Config")

    st.markdown("### 📁 File 1 — Stock & Sales")
    file_stock = st.file_uploader(
        "sku_code · product_name · tanggal_update · stock · qpd · doi",
        type=["csv"], key="file_stock",
    )

    st.markdown("### 📁 File 2 — Lead Times")
    file_leadtime = st.file_uploader(
        "sku_code · supplier · lead_time_days",
        type=["csv"], key="file_leadtime",
    )

    st.markdown("### 📁 File 3 — Active Supplier per SKU")
    file_supplier = st.file_uploader(
        "sku_code · supplier  (one row per SKU)",
        type=["csv"], key="file_supplier",
    )

    all_files_uploaded = (file_stock is not None and
                          file_leadtime is not None and
                          file_supplier is not None)

    st.markdown("### 🔄 Reorder Trigger (RT)")
    col1, col2 = st.columns(2)
    with col1:
        rt_start = st.number_input("Start",  min_value=1, max_value=99,  value=21, step=1)
    with col2:
        rt_stop  = st.number_input("Stop ①", min_value=2, max_value=100, value=22, step=1)

    st.markdown("### 🎯 Target DOI")
    col3, col4 = st.columns(2)
    with col3:
        doi_start = st.number_input("Start ",  min_value=1,   max_value=364, value=27, step=1)
    with col4:
        doi_stop  = st.number_input("Stop ①",  min_value=2,   max_value=365, value=30, step=1)

    st.markdown("### 🏭 Capacity Limits")
    daily_cap = st.number_input("Daily SKU Capacity", min_value=1, value=360,  step=10,
                                help="Max unique SKUs your warehouse processes per day")
    total_cap = st.number_input("Total SKU Capacity", min_value=1, value=5100, step=100,
                                help="Total unique SKU count the warehouse can hold")

    st.markdown("### 📆 Simulation Period")
    start_date = st.date_input("Start Date", value=date(2026, 2, 1))
    end_date   = st.date_input("End Date",   value=date(2026, 3, 31))

    st.markdown("### 💾 Output Options")
    save_detailed = st.checkbox("Save detailed per-SKU results", value=True)
    save_daily    = st.checkbox("Save daily summaries",          value=True)

    st.markdown("---")
    st.caption("① Stop is exclusive — same as Python's range()")


# ─────────────────────────────────────────────────────────────
# Join helper — cached on file content bytes
# Returns (merged_df, unmatched_df)
# ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def build_merged_df(stock_bytes: bytes, leadtime_bytes: bytes, supplier_bytes: bytes):
    df1 = pd.read_csv(io.BytesIO(stock_bytes))
    df2 = pd.read_csv(io.BytesIO(leadtime_bytes))
    df3 = pd.read_csv(io.BytesIO(supplier_bytes))

    # Step 1 — File 2 RIGHT JOIN File 3 on (sku_code + supplier)
    # File 3 drives the result — every active SKU×supplier is kept.
    # SKUs in File 3 with no matching entry in File 2 → lead_time_days = NaN (unmatched)
    # SKUs in File 2 with no active supplier in File 3 → dropped
    active_lt = df2[["sku_code", "supplier", "lead_time_days"]].merge(
        df3[["sku_code", "supplier"]],
        on=["sku_code", "supplier"],
        how="right",
    )

    # Step 2 — INNER JOIN active_lt with File 1 on sku_code
    # Only SKUs that exist in BOTH File 3 AND File 1 proceed.
    # SKUs in File 3 with no stock data in File 1 → dropped
    # SKUs in File 1 not listed in File 3 → dropped (no active supplier)
    merged = df1.merge(
        active_lt[["sku_code", "lead_time_days"]],
        on="sku_code",
        how="inner",
    )

    # Identify unmatched SKUs (in File 3 but no lead time in File 2)
    unmatched_skus = (
        merged[merged["lead_time_days"].isna()]["sku_code"]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    return merged, unmatched_skus


# ─────────────────────────────────────────────────────────────
# Config writer
# ─────────────────────────────────────────────────────────────
def write_config(work_dir: str, output_dir: str, csv_path: str) -> None:
    content = textwrap.dedent(f"""\
        REORDER_THRESHOLD_RANGE = range({rt_start}, {rt_stop + 1})
        TARGET_DOI_RANGE        = range({doi_start}, {doi_stop + 1})
        DAILY_SKU_CAPACITY      = {daily_cap}
        TOTAL_SKU_CAPACITY      = {total_cap}
        START_DATE = ({start_date.year}, {start_date.month}, {start_date.day})
        END_DATE   = ({end_date.year},   {end_date.month},   {end_date.day})
        DATA_FILE  = r'{csv_path}'
        OUTPUT_DIR = r'{output_dir}'
        SAVE_DETAILED_RESULTS = {save_detailed}
        SAVE_DAILY_SUMMARIES  = {save_daily}
    """)
    with open(os.path.join(work_dir, "config.py"), "w") as f:
        f.write(content)


# ─────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────
st.title("📦 Supply Chain Simulation")
st.caption("Upload your 3 data files in the sidebar, configure parameters, then click **Run Simulation**.")

n_rt        = max(0, rt_stop  - rt_start  + 1)
n_doi       = max(0, doi_stop - doi_start + 1)
n_scenarios = n_rt * n_doi

c1, c2, c3 = st.columns(3)
c1.metric("RT values to test",  n_rt,        f"RT {rt_start} → {rt_stop}")
c2.metric("DOI values to test", n_doi,       f"DOI {doi_start} → {doi_stop}")
c3.metric("Total scenarios",    n_scenarios, "combinations")

st.divider()

# ─────────────────────────────────────────────────────────────
# Data Preparation
# ─────────────────────────────────────────────────────────────
st.markdown("### 📂 Data Preparation")

s1, s2, s3 = st.columns(3)
s1.markdown(("✅" if file_stock    else "⏳") + " **File 1** — Stock & Sales\n\n" +
            (f"`{file_stock.name}`"    if file_stock    else "*not uploaded*"))
s2.markdown(("✅" if file_leadtime else "⏳") + " **File 2** — Lead Times\n\n" +
            (f"`{file_leadtime.name}`" if file_leadtime else "*not uploaded*"))
s3.markdown(("✅" if file_supplier else "⏳") + " **File 3** — Active Supplier\n\n" +
            (f"`{file_supplier.name}`" if file_supplier else "*not uploaded*"))

edited_unmatched = None
has_unresolved   = False
merged_df        = None

if all_files_uploaded:
    with st.spinner("Joining files..."):
        try:
            merged_df, unmatched_skus = build_merged_df(
                file_stock.getvalue(),
                file_leadtime.getvalue(),
                file_supplier.getvalue(),
            )
        except Exception as e:
            st.error(f"❌ Error joining files: {e}")
            st.stop()

    n_total     = merged_df["sku_code"].nunique()
    n_unmatched = len(unmatched_skus)
    n_matched   = n_total - n_unmatched

    st.markdown("**Join Results:**")
    jc1, jc2, jc3 = st.columns(3)
    jc1.metric("Total unique SKUs", n_total)
    jc2.metric("✅ Matched",        n_matched,   help="Lead time found via File 2 × File 3")
    jc3.metric("⚠️ Unmatched",      n_unmatched, help="No lead time in File 2 for their active supplier — a single default will be applied")

    if n_unmatched > 0:
        st.warning(
            f"**{n_unmatched} SKU(s) have no lead time.** "
            "Their active supplier (File 3) has no matching entry in the lead time table (File 2). "
            "This is expected when a SKU × supplier combination has no historical data. "
            "The lead time below will be applied to all of them."
        )

        # Show the list of affected SKUs so the user knows which ones will use the default
        with st.expander(f"View {n_unmatched} unmatched SKU(s)", expanded=False):
            st.dataframe(
                merged_df[merged_df["lead_time_days"].isna()][["sku_code", "product_name"]]
                .drop_duplicates(subset="sku_code")
                .reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
            )

        default_lt = st.number_input(
            "Default lead time for all unmatched SKUs (working days)",
            min_value=1,
            max_value=365,
            value=14,
            step=1,
            help="This value will be applied to every SKU that has no lead time entry in File 2.",
            key="default_lt_input",
        )
        edited_unmatched = default_lt
        has_unresolved   = False  # always resolved — user just picks a number
        st.success(f"✅ {n_unmatched} unmatched SKU(s) will use lead time = **{default_lt} days**. Ready to run.")
    else:
        edited_unmatched = None
        st.success("✅ All SKUs matched successfully. No manual input needed.")

    with st.expander("🔍 Preview merged data (first 20 rows)", expanded=False):
        preview = merged_df.copy()
        if isinstance(edited_unmatched, int) or isinstance(edited_unmatched, float):
            preview["lead_time_days"] = preview["lead_time_days"].fillna(edited_unmatched)
        st.dataframe(preview.head(20), use_container_width=True, hide_index=True)

else:
    st.info("Upload all 3 files in the sidebar to validate and preview your data.")

st.divider()

# ─────────────────────────────────────────────────────────────
# Run button
# ─────────────────────────────────────────────────────────────
run_ready   = all_files_uploaded and not has_unresolved
run_clicked = st.button(
    "▶  Run Simulation",
    type="primary",
    use_container_width=True,
    disabled=not run_ready,
)

if run_clicked:
    # Build final CSV with all lead times resolved
    final_df = merged_df.copy()

    if isinstance(edited_unmatched, (int, float)) and edited_unmatched > 0:
        # Apply the single default lead time to all unmatched SKUs
        final_df["lead_time_days"] = final_df["lead_time_days"].fillna(edited_unmatched)

    if final_df["lead_time_days"].isna().any():
        st.error("Some SKUs still have no lead time. Please fill in all values and try again.")
        st.stop()

    run_id   = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = tempfile.mkdtemp(prefix="sim_work_")
    out_dir  = tempfile.mkdtemp(prefix=f"sim_out_{run_id}_")

    csv_path = os.path.join(work_dir, "merged_data.csv")
    final_df.to_csv(csv_path, index=False)

    write_config(work_dir, out_dir, csv_path)

    sim_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulation_plotly.py")
    if not os.path.exists(sim_src):
        st.error("simulation_plotly.py not found next to app2_plotly.py.")
        st.stop()

    import shutil
    shutil.copy(sim_src, os.path.join(work_dir, "simulation_plotly.py"))

    st.markdown("### 🖥️ Simulation Log")
    log_placeholder    = st.empty()
    status_placeholder = st.empty()
    log_lines          = []

    def render_log():
        log_placeholder.markdown(
            f'<div class="log-box">{"".join(log_lines)}</div>',
            unsafe_allow_html=True,
        )

    status_placeholder.info("⏳  Simulation running — this may take a few minutes…")

    proc = subprocess.Popen(
        [sys.executable, "simulation_plotly.py"],
        cwd=work_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        log_lines.append(line)
        render_log()
    proc.wait()
    success = (proc.returncode == 0)

    if success:
        status_placeholder.success("✅  Simulation completed successfully!")
    else:
        status_placeholder.error("❌  Simulation failed — see log above for details.")
        st.stop()

    st.divider()
    st.markdown("## 📊 Results")

    csv_files = glob.glob(os.path.join(out_dir, "scenario_comparison_summary_byday_*.csv"))
    if csv_files:
        df = pd.read_csv(csv_files[0])
        st.markdown("### 📋 Scenario Comparison Table")
        st.dataframe(df, use_container_width=True, hide_index=True)

        if "Days_Over_Capacity" in df.columns:
            best_row = df.loc[df["Days_Over_Capacity"].idxmin()]
            st.markdown("#### 🏆 Best Scenario (fewest days over capacity)")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Scenario",           best_row.get("Scenario", "—"))
            m2.metric("Days Over Capacity", int(best_row.get("Days_Over_Capacity", 0)))
            m3.metric("Capacity Util %",    f"{best_row.get('Capacity_Utilization_Pct', 0):.1f}%")
            m4.metric("Stockout Rate %",    f"{best_row.get('Stockout_Rate_Pct', 0):.2f}%")

    html_files = sorted(glob.glob(os.path.join(out_dir, "*.html")))
    if html_files:
        st.markdown("### 📈 Interactive Charts")
        for html_path in html_files:
            chart_name = os.path.basename(html_path).replace("_", " ").replace(".html", "").title()
            with st.expander(f"📊 {chart_name}", expanded=False):
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                components.html(html_content, height=800, scrolling=True)

    st.markdown("### ⬇️ Download All Results")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in glob.glob(os.path.join(out_dir, "*")):
            zf.write(fpath, arcname=os.path.basename(fpath))
    zip_buffer.seek(0)
    st.download_button(
        label="📥 Download Results ZIP (CSVs + Charts)",
        data=zip_buffer,
        file_name=f"simulation_results_{run_id}.zip",
        mime="application/zip",
        use_container_width=True,
        type="primary",
    )

else:
    st.markdown("""
    <div class="run-box">
    <strong>How to use:</strong><br>
    1. Upload <strong>File 1</strong> (Stock & Sales), <strong>File 2</strong> (Lead Times),
       and <strong>File 3</strong> (Active Supplier) using the sidebar<br>
    2. The app automatically joins the files and flags any SKUs with no lead time<br>
    3. Fill in lead times manually for any flagged SKUs in the table above<br>
    4. Adjust simulation parameters in the sidebar as needed<br>
    5. Click <strong>▶ Run Simulation</strong>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("ℹ️ File Format Guide"):
        st.markdown("""
        | File | Required Columns | Notes |
        |---|---|---|
        | **File 1 — Stock & Sales** | `sku_code`, `product_name`, `tanggal_update`, `stock`, `qpd`, `doi` | One row per SKU per date |
        | **File 2 — Lead Times** | `sku_code`, `supplier`, `lead_time_days` | One SKU can have multiple suppliers |
        | **File 3 — Active Supplier** | `sku_code`, `supplier` | One row per SKU — the currently active supplier |

        **How the join works:**
        File 3 identifies the active supplier for each SKU.
        File 2 provides the lead time for each SKU × supplier combination.
        These two are inner-joined to get one lead time per SKU, then merged with File 1.
        SKUs whose active supplier has no entry in File 2 will appear in the manual input table.
        """)

    with st.expander("ℹ️ Parameter Guide"):
        st.markdown("""
        | Parameter | What it does |
        |---|---|
        | **RT Start / Stop** | Range of Reorder Trigger (DOI threshold) values to test |
        | **DOI Start / Stop** | Range of Target Days-of-Inventory values to test |
        | **Daily SKU Capacity** | Max unique SKUs the inbound team can receive per day |
        | **Total SKU Capacity** | Total unique SKUs the warehouse can hold |
        | **Start / End Date** | The simulation reporting period |
        """)
