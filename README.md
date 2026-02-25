# 📦 Supply Chain Inbound Simulation & Scenario Analysis

> A browser-based simulation tool that helps supply chain teams evaluate inventory reorder policies across multiple scenarios — without writing a single line of code.

## 🎯 Background & Problem Statement

As a **Business Intelligence staff** at a distribution company, I identified a recurring operational problem: the supply chain team had no reliable way to evaluate how different reorder policies would affect daily warehouse inbound capacity.

The questions they needed answered:
- *"If we lower our reorder threshold from 25 to 20 days, how many more SKUs will arrive per day?"*
- *"Which DOI target keeps us under our 360-SKU/day processing limit?"*
- *"Which day of the week gets overloaded most under each scenario?"*

Previously, this analysis was either skipped entirely or done manually in spreadsheets — time-consuming and error-prone. I built this tool to **automate the full simulation pipeline** and surface the results interactively, so the supply chain team could explore scenarios themselves without any Python knowledge.

---

## 🚀 Live Demo

🔗 **(https://inboundsim.streamlit.app)**

> Upload any compatible inventory CSV to try it live.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Multi-scenario simulation** | Tests every combination of RT × DOI in a single run |
| **SKU-level simulation** | Tracks each SKU's daily stock, sales, orders in transit, and DOI |
| **Working-day lead times** | Order arrival is calculated in working days, skipping weekends |
| **7 interactive Plotly charts** | Grouped bar charts, boxplots — fully zoomable and hoverable |
| **Live log streaming** | Watch the simulation progress line by line in the browser |
| **Auto best-scenario detection** | Highlights the scenario with fewest capacity overload days |
| **ZIP download** | All CSVs + HTML charts packaged for offline sharing |
| **Zero-code interface** | Supply chain team uploads CSV, sets parameters, clicks Run |

---

## 🧠 Simulation Logic

The engine implements a **continuous review (s, S) inventory policy** — a standard model in supply chain management — applied independently to each SKU:

```
For each SKU × each day in the simulation period:

  1.  Receive stock from any orders arriving today
      (arrival date = order date + lead_time_days working days)

  2.  Deduct daily sales (QPD = quantity per day, from historical data)

  3.  Compute DOI = current_stock / QPD

  4.  Reorder trigger fires when:
        DOI ≤ Reorder Threshold  AND  no order currently in transit

  5.  If triggered, calculate order quantity:
        Q = (Target_DOI + estimated_calendar_days) × QPD − current_stock
        where estimated_calendar_days = lead_time_days × 1.17

  6.  Schedule arrival = add_working_days(today, lead_time_days)
```

The simulation then **aggregates across all SKUs per day** to compute the daily inbound workload — which is what the warehouse team actually cares about.

---

## 📊 Output Metrics (per scenario)

| Metric | Description |
|---|---|
| `Avg_Daily_SKUs` | Average unique SKUs arriving per day |
| `Max_Daily_SKUs` | Single worst-day peak inbound volume |
| `Days_Over_Capacity` | Days where arrivals exceed the daily SKU limit |
| `Pct_Days_Over_Capacity` | % of simulation days in overload |
| `Capacity_Utilization_Pct` | Avg daily SKUs as % of daily capacity |
| `Total_Orders` | Total purchase orders placed across all SKUs |
| `Avg_DOI` | Average days of inventory maintained |
| `Overload_[Weekday]` | Overload days broken down by day of week |
| `Avg_[Weekday]` | Average arrivals per weekday |

---

## 📈 Charts Generated

All charts are saved as interactive `.html` files (Plotly) and rendered directly in the browser:

| # | Chart | Key question answered |
|---|---|---|
| 1 | Overload Days by DOI — grouped by RT | Which DOI targets cause the most overload? |
| 2 | Avg Arrivals by DOI — grouped by RT | How does DOI choice spread arrivals across weekdays? |
| 3 | Binning Distribution by DOI — grouped by RT | How many days fall into each arrival volume bucket? |
| 4 | Avg Arrivals by RT — grouped by DOI | How does the reorder threshold affect arrival patterns? |
| 5 | Overload Days by RT — grouped by DOI | Which RT values create the most overloaded days? |
| 6 | Binning Distribution by RT — grouped by DOI | Arrival volume distribution per RT value |
| 7 | Boxplot of Daily Arrivals — grouped by RT | Variance and outliers in daily inbound per scenario |

---

## 🛠️ Tech Stack

| | Technology | Why |
|---|---|---|
| **Web framework** | Streamlit | Rapid deployment of data apps, no frontend code needed |
| **Charts** | Plotly (Graph Objects) | Fully interactive, exportable as standalone HTML |
| **Data processing** | Pandas, NumPy | Vectorised operations for multi-SKU daily simulation |
| **Subprocess isolation** | Python `subprocess` | Each run gets a fresh process + config, no state leaks between sessions |

---

## 📁 Repository Structure

```
📁 supply-chain-simulation/
│
├── app2_plotly.py          # Streamlit web app — UI, config form, subprocess runner
├── simulation_plotly.py    # Simulation engine — inventory logic + Plotly chart generation
└── requirements.txt        # Python dependencies
```

### Architecture Decision

`app2_plotly.py` and `simulation_plotly.py` are **intentionally decoupled**:

- The app writes a `config.py` at runtime from user inputs
- The simulation runs as a **subprocess** reading that config
- This means the simulation logic can be updated, tested, or even run standalone without touching the web app

This pattern also ensures **complete isolation between user sessions** on a shared deployment — each run gets its own temp directory and config file.

---

## 📋 Input Data Format

The CSV file must contain the following columns:

| Column | Type | Description |
|---|---|---|
| `tanggal_update` | `date` | Inventory snapshot date (used to pick starting stock) |
| `sku_code` | `string` | Unique SKU identifier |
| `product_name` | `string` | Product display name |
| `stock` | `float` | Current stock quantity on the snapshot date |
| `qpd` | `float` | Average quantity sold per day (historical) |
| `doi` | `float` | Current days-of-inventory on the snapshot date |
| `lead_time_days` | `int` | Working days from purchase order to warehouse arrival |

SKUs with `qpd = 0` or null are automatically excluded from the simulation.

---

## 🖥️ How It Works — User Flow

```
1. Open the app in browser
       ↓
2. Upload CSV data file (sidebar)
       ↓
3. Set parameters:
   · Reorder Threshold range (RT start → stop)
   · Target DOI range (DOI start → stop)
   · Daily & Total SKU capacity limits
   · Simulation date range
       ↓
4. Click ▶ Run Simulation
       ↓
5. Watch live log as each scenario runs
       ↓
6. View results:
   · Scenario comparison table
   · Best scenario highlighted automatically
   · 7 interactive Plotly charts
       ↓
7. Download ZIP (CSVs + HTML charts)
```

---

## 💡 Key Design Choices

**Why Streamlit over a pure Python script?**
The supply chain team has no Python access. Packaging this as a browser app meant zero installation on their end — they open a URL and use it like any web tool.

**Why Plotly over Matplotlib?**
Plotly charts are interactive HTML files — the team can zoom, hover for exact values, toggle series on/off, and share the HTML files as standalone reports. Static PNGs can't do any of that.


---

## 👤 About

Built by **Jefta Wuarlela** · Business Intelligence

- 🔗 [LinkedIn](https://linkedin.com/in/jefta-ferdinand-737979220)
- 📧 jefta.wuarlela@gmail.com
- 🗂️ [More projects](https://github.com/jeftawuarlela-jf)

---

*This project was built to solve a real operational problem — translating a business question about warehouse capacity into a self-service tool that a non-technical team can use independently.*
