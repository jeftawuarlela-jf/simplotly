import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
import random
import os
from itertools import product

# Import configuration
try:
    from config import *
    print("✓ Configuration loaded from config.py")
except ImportError:
    print("⚠️  config.py not found, using default values")
    REORDER_THRESHOLD_RANGE = 20
    TARGET_DOI_RANGE = 35
    DAILY_SKU_CAPACITY = 360
    TOTAL_SKU_CAPACITY = 5100
    START_DATE = (2025, 7, 1)
    END_DATE = (2025, 12, 31)
    DATA_FILE = 'fulllead.csv'
    OUTPUT_DIR = 'simulation_results'
    SAVE_DETAILED_RESULTS = True
    SAVE_DAILY_SUMMARIES = True

if isinstance(START_DATE, tuple):
    START_DATE = datetime(*START_DATE)
if isinstance(END_DATE, tuple):
    END_DATE = datetime(*END_DATE)

# ========================================
# HELPER FUNCTIONS
# ========================================
def add_working_days(start_date, working_days):
    current_date = start_date
    days_added = 0
    while days_added < working_days:
        current_date += timedelta(days=1)
        if current_date.weekday() < 6:
            days_added += 1
    return current_date

def run_single_simulation(sku_info, reorder_threshold, target_doi, date_range):
    has_price = 'net_price' in sku_info.columns
    results = []

    for idx, sku_row in sku_info.iterrows():
        sku_code = sku_row['sku_code']
        product_name = sku_row['product_name']
        stock = sku_row['stock']
        quantity_sold_per_day = sku_row['quantity_sold_per_day']
        lead_time_days = int(sku_row['lead_time_days'])
        net_price = float(sku_row['net_price']) if has_price and pd.notna(sku_row.get('net_price')) else 0.0

        if quantity_sold_per_day == 0 or pd.isna(quantity_sold_per_day):
            continue

        orders_in_transit = []

        for date in date_range:
            stock_beginning = stock

            arriving_orders = [order for order in orders_in_transit if order[0] == date]
            stock_received = sum([order[1] for order in arriving_orders])
            stock += stock_received

            orders_in_transit = [order for order in orders_in_transit if order[0] != date]

            sales = quantity_sold_per_day
            stock -= sales

            doi = stock / quantity_sold_per_day if quantity_sold_per_day > 0 else 999

            total_in_transit = sum([order[1] for order in orders_in_transit])

            reorder_trigger = (doi <= reorder_threshold) and (len(orders_in_transit) == 0)

            order_placed = False
            order_quantity = 0

            if reorder_trigger:
                estimated_calendar_days = lead_time_days * 1.17
                raw_order_quantity = (target_doi + estimated_calendar_days) * quantity_sold_per_day - stock
                order_quantity = math.ceil(raw_order_quantity)  # Round UP

                if order_quantity > 0:
                    order_placed = True
                    arrival_date = add_working_days(date, lead_time_days)
                    orders_in_transit.append((arrival_date, order_quantity))

            # Value calculation: floor(ending_stock * net_price)
            if stock_received > 0:
                stock_received_value = math.floor(stock) * net_price  # ending_stock * net_price, rounded DOWN
            else:
                stock_received_value = 0.0

            results.append({
                'date': date,
                'sku_code': sku_code,
                'product_name': product_name,
                'lead_time_days': lead_time_days,
                'net_price': net_price,
                'stock_beginning': stock_beginning,
                'sales': sales,
                'stock_received': stock_received,
                'stock_received_value': stock_received_value,
                'stock_ending': stock,
                'doi': doi,
                'order_placed': order_placed,
                'order_quantity': order_quantity,
                'orders_in_transit_qty': total_in_transit,
                'orders_in_transit_count': len(orders_in_transit)
            })

    return pd.DataFrame(results)

def analyze_simulation(results_df, reorder_threshold, target_doi, date_range):
    # ── Unique SKUs arrived per day (existing metric) ──
    daily_arrivals = results_df[results_df['stock_received'] > 0].groupby('date').agg(
        unique_skus_arrived=('sku_code', 'count')
    ).reset_index()

    # ── Inbound VOLUME: total quantity arrived per day (NOT unique SKUs) ──
    daily_volume = results_df.groupby('date').agg(
        inbound_quantity=('stock_received', 'sum')
    ).reset_index()

    # ── Inbound VALUE: sum of (net_price * qty) per day ──
    daily_value = results_df.groupby('date').agg(
        inbound_value=('stock_received_value', 'sum')
    ).reset_index()

    # Merge all daily metrics
    all_dates = pd.DataFrame({'date': date_range})
    daily_arrivals = all_dates.merge(daily_arrivals, on='date', how='left').fillna(0)
    daily_arrivals = daily_arrivals.merge(daily_volume, on='date', how='left').fillna(0)
    daily_arrivals = daily_arrivals.merge(daily_value, on='date', how='left').fillna(0)

    daily_arrivals['day_of_week'] = daily_arrivals['date'].dt.day_name()

    # Statistics
    avg_daily_skus = daily_arrivals['unique_skus_arrived'].mean()
    max_daily_skus = daily_arrivals['unique_skus_arrived'].max()
    median_daily_skus = daily_arrivals['unique_skus_arrived'].median()
    std_daily_skus = daily_arrivals['unique_skus_arrived'].std()

    # Volume stats
    avg_daily_volume = daily_arrivals['inbound_quantity'].mean()
    max_daily_volume = daily_arrivals['inbound_quantity'].max()
    total_volume = daily_arrivals['inbound_quantity'].sum()

    # Value stats
    avg_daily_value = daily_arrivals['inbound_value'].mean()
    max_daily_value = daily_arrivals['inbound_value'].max()
    total_value = daily_arrivals['inbound_value'].sum()

    days_over_capacity = (daily_arrivals['unique_skus_arrived'] > DAILY_SKU_CAPACITY).sum()

    # Binning analysis (EXCLUDING SUNDAYS)
    bins = [0, 30, 90, 180, 270, 360, 540, 720, float('inf')]
    bin_labels = ['0-30', '31-90', '91-180', '181-270', '271-360', '361-540', '541-720', '720+']

    daily_arrivals_no_sunday = daily_arrivals[daily_arrivals['day_of_week'] != 'Sunday'].copy()
    daily_arrivals_no_sunday['bin'] = pd.cut(daily_arrivals_no_sunday['unique_skus_arrived'],
                                              bins=bins, labels=bin_labels, include_lowest=True)
    bin_counts = daily_arrivals_no_sunday['bin'].value_counts().sort_index()
    bin_distribution = dict(zip(bin_labels, [bin_counts.get(label, 0) for label in bin_labels]))

    total_unique_skus_arrived = results_df[results_df['stock_received'] > 0]['sku_code'].nunique()
    avg_doi = results_df['doi'].mean()
    total_orders = results_df['order_placed'].sum()

    daily_arrivals['is_overload'] = daily_arrivals['unique_skus_arrived'] > DAILY_SKU_CAPACITY
    overload_by_day = daily_arrivals.groupby('day_of_week')['is_overload'].sum().to_dict()
    avg_arrivals_by_day = daily_arrivals.groupby('day_of_week')['unique_skus_arrived'].mean().to_dict()

    # Volume & Value by day of week
    avg_volume_by_day = daily_arrivals.groupby('day_of_week')['inbound_quantity'].mean().to_dict()
    avg_value_by_day = daily_arrivals.groupby('day_of_week')['inbound_value'].mean().to_dict()

    return {
        'reorder_threshold': reorder_threshold,
        'target_doi': target_doi,
        'avg_daily_skus': avg_daily_skus,
        'max_daily_skus': max_daily_skus,
        'median_daily_skus': median_daily_skus,
        'std_daily_skus': std_daily_skus,
        'days_over_capacity': days_over_capacity,
        'pct_days_over_capacity': (days_over_capacity / len(date_range) * 100),
        'capacity_utilization': (avg_daily_skus / DAILY_SKU_CAPACITY * 100),
        'total_unique_skus_arrived': total_unique_skus_arrived,
        'total_capacity_utilization': (total_unique_skus_arrived / TOTAL_SKU_CAPACITY * 100),
        'total_orders': total_orders,
        'avg_doi': avg_doi,
        'daily_arrivals': daily_arrivals,
        'overload_by_day': overload_by_day,
        'avg_arrivals_by_day': avg_arrivals_by_day,
        'bin_distribution': bin_distribution,
        # New: volume & value metrics
        'avg_daily_volume': avg_daily_volume,
        'max_daily_volume': max_daily_volume,
        'total_volume': total_volume,
        'avg_daily_value': avg_daily_value,
        'max_daily_value': max_daily_value,
        'total_value': total_value,
        'avg_volume_by_day': avg_volume_by_day,
        'avg_value_by_day': avg_value_by_day,
    }

# ========================================
# MAIN EXECUTION
# ========================================
def main():
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"Run ID: {run_id}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Loading data...")
    df = pd.read_csv(DATA_FILE)
    df['tanggal_update'] = pd.to_datetime(df['tanggal_update'])

    has_price = 'net_price' in df.columns
    if has_price:
        print("✓ net_price column found — value tracking enabled")
    else:
        print("⚠️  net_price column not found — value tracking disabled, volume still tracked")

    print(f"\nPreparing starting inventory from: {START_DATE.date()}...")
    starting_data = df[df['tanggal_update'] == START_DATE].copy()
    if len(starting_data) == 0:
        starting_data = df[df['tanggal_update'] == df['tanggal_update'].min()].copy()

    agg_dict = {
        'product_name': 'first',
        'stock': 'first',
        'quantity_sold_per_day': 'first',
        'doi': 'first',
        'lead_time_days': 'first'
    }
    if has_price:
        agg_dict['net_price'] = 'first'

    sku_info = starting_data.groupby('sku_code').agg(agg_dict).reset_index()

    if has_price:
        sku_info['net_price'] = sku_info['net_price'].fillna(0)

    print(f"Starting with {len(sku_info)} unique SKUs")
    print(f"Lead time range: {sku_info['lead_time_days'].min():.0f} to {sku_info['lead_time_days'].max():.0f} working days")

    date_range = pd.date_range(START_DATE, END_DATE, freq='D')
    param_combinations = list(product(REORDER_THRESHOLD_RANGE, TARGET_DOI_RANGE))
    total_scenarios = len(param_combinations)

    all_scenario_results = []
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    for scenario_num, (reorder_threshold, target_doi) in enumerate(param_combinations, 1):
        print(f"\nScenario {scenario_num}/{total_scenarios}: Reorder Threshold={reorder_threshold}, Target DOI={target_doi}")

        results_df = run_single_simulation(sku_info, reorder_threshold, target_doi, date_range)
        analysis = analyze_simulation(results_df, reorder_threshold, target_doi, date_range)
        all_scenario_results.append(analysis)

        if SAVE_DETAILED_RESULTS:
            scenario_filename = f"scenario_RT{reorder_threshold}_DOI{target_doi}_detailed2.csv"
            results_df.to_csv(os.path.join(OUTPUT_DIR, scenario_filename), index=False)
            print(f"\n  ✓ Saved: {scenario_filename}")

        if SAVE_DAILY_SUMMARIES:
            daily_filename = f"scenario_RT{reorder_threshold}_DOI{target_doi}_daily2.csv"
            analysis['daily_arrivals'].to_csv(os.path.join(OUTPUT_DIR, daily_filename), index=False)

    # ========================================
    # COMPARISON SUMMARY
    # ========================================
    print(f"\n{'='*60}")
    print("CREATING COMPARISON SUMMARY")
    print(f"{'='*60}")

    comparison_rows = []
    for r in all_scenario_results:
        row = {
            'Scenario': f"RT{r['reorder_threshold']}_DOI{r['target_doi']}",
            'Reorder_Threshold': r['reorder_threshold'],
            'Target_DOI': r['target_doi'],
            'Avg_Daily_SKUs': round(r['avg_daily_skus'], 2),
            'Max_Daily_SKUs': int(r['max_daily_skus']),
            'Days_Over_Capacity': int(r['days_over_capacity']),
            'Pct_Days_Over_Capacity': round(r['pct_days_over_capacity'], 2),
            'Capacity_Utilization_Pct': round(r['capacity_utilization'], 2),
            'Total_Orders': int(r['total_orders']),
            'StDev_Daily_SKUs': round(r['std_daily_skus'], 2),
            # Volume & Value
            'Avg_Daily_Inbound_Qty': round(r['avg_daily_volume'], 2),
            'Max_Daily_Inbound_Qty': round(r['max_daily_volume'], 2),
            'Total_Inbound_Qty': round(r['total_volume'], 2),
            'Avg_Daily_Inbound_Value': round(r['avg_daily_value'], 2),
            'Max_Daily_Inbound_Value': round(r['max_daily_value'], 2),
            'Total_Inbound_Value': round(r['total_value'], 2),
        }
        for day in day_order:
            row[f'Overload_{day}'] = int(r['overload_by_day'].get(day, 0))
        for day in day_order:
            row[f'Avg_{day}'] = round(r['avg_arrivals_by_day'].get(day, 0), 2)
        comparison_rows.append(row)

    comparison_df = pd.DataFrame(comparison_rows)
    comparison_df = comparison_df.sort_values(['Reorder_Threshold', 'Target_DOI'])
    comparison_df.to_csv(os.path.join(OUTPUT_DIR, f'scenario_comparison_summary_byday_{run_id}.csv'), index=False)

    print("\n" + "="*60)
    print("SCENARIO COMPARISON TABLE")
    print("="*60)
    print(comparison_df.to_string(index=False))

    best_capacity = comparison_df.loc[comparison_df['Days_Over_Capacity'].idxmin()]
    print(f"\n✓ Best for capacity (fewest days over limit):")
    print(f"  Scenario: {best_capacity['Scenario']}")
    print(f"  Days over capacity: {best_capacity['Days_Over_Capacity']}")
    print(f"  Capacity utilization: {best_capacity['Capacity_Utilization_Pct']:.1f}%")

    # ========================================
    # CHARTS SETUP
    # ========================================
    print("\n" + "="*60)
    print("CREATING ALL VISUALIZATIONS (Plotly Interactive Charts)")
    print("="*60)

    reorder_thresholds = sorted(set(r['reorder_threshold'] for r in all_scenario_results))
    target_dois = sorted(set(r['target_doi'] for r in all_scenario_results))
    num_thresholds = len(reorder_thresholds)
    num_dois = len(target_dois)

    day_colors_list = px.colors.qualitative.Set2[:len(day_order)]
    day_color_map = {day: day_colors_list[i] for i, day in enumerate(day_order)}

    doi_colors_list = px.colors.qualitative.Set2[:len(target_dois)]
    doi_color_map = {doi: doi_colors_list[i] for i, doi in enumerate(target_dois)}

    bin_labels = ['0-30', '31-90', '91-180', '181-270', '271-360', '361-540', '541-720', '720+']
    bin_color_map = {
        '0-30':    '#2d9a2d',
        '31-90':   '#4caf50',
        '91-180':  '#7bc67e',
        '181-270': '#ffff00',
        '271-360': '#ffcc80',
        '361-540': '#ff9800',
        '541-720': '#f44336',
        '720+':    '#b71c1c',
    }

    all_avg_values = [r['avg_arrivals_by_day'].get(d, 0) for r in all_scenario_results for d in day_order]
    y_max_avg = max(all_avg_values) * 1.20

    all_overload_values = [int(r['overload_by_day'].get(d, 0)) for r in all_scenario_results for d in day_order]
    y_max_overload = max(all_overload_values) * 1.20 if max(all_overload_values) > 0 else 10

    all_bin_values = [int(r['bin_distribution'].get(bl, 0)) for r in all_scenario_results for bl in bin_labels]
    y_max_bin = max(all_bin_values) * 1.20 if max(all_bin_values) > 0 else 10

    all_box_values = []
    for r in all_scenario_results:
        arrivals = r['daily_arrivals']
        filtered = arrivals[arrivals['day_of_week'] != 'Sunday']['unique_skus_arrived'].values
        if len(filtered) > 0:
            all_box_values.append(filtered.max())
    y_max_box = max(all_box_values) * 1.15 if all_box_values else 1000

    # Global y-max for volume and value charts
    all_vol_values = [r['avg_volume_by_day'].get(d, 0) for r in all_scenario_results for d in day_order]
    y_max_vol = max(all_vol_values) * 1.20 if max(all_vol_values) > 0 else 100

    all_val_values = [r['avg_value_by_day'].get(d, 0) for r in all_scenario_results for d in day_order]
    y_max_val = max(all_val_values) * 1.20 if max(all_val_values) > 0 else 100

    # ========================================
    # CHART 1: Overload Days by DOI — Grouped by RT
    # ========================================
    fig1 = make_subplots(rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        for i, day in enumerate(day_order):
            day_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                day_values.append(int(match['overload_by_day'].get(day, 0)) if match else 0)
            fig1.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois], y=day_values, name=day,
                marker_color=day_color_map[day], opacity=0.8, text=day_values,
                textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=day,
            ), row=row_idx, col=1)
        fig1.update_yaxes(title_text='Number of Overload Days', range=[0, y_max_overload], row=row_idx, col=1)
        fig1.update_xaxes(title_text='Target DOI', row=row_idx, col=1)

    fig1.update_layout(barmode='group',
        title_text=f'Overload Days by Target DOI — Grouped by Reorder Threshold<br><sup>(Days Exceeding {DAILY_SKU_CAPACITY} SKU Capacity)</sup>',
        title_font_size=16, height=500 * num_thresholds, autosize=True, legend_title_text='Day of Week')
    fig1.write_json(os.path.join(OUTPUT_DIR, f'comparison_overload_days_bydoi_grouped_by_rt_{run_id}.json'))
    fig1.write_html(os.path.join(OUTPUT_DIR, f'comparison_overload_days_bydoi_grouped_by_rt_{run_id}.html'))
    print("  ✓ Chart 1: Overload Days by DOI (grouped by RT)")

    # ========================================
    # CHART 2: Avg Arrivals by DOI — Grouped by RT
    # ========================================
    fig2 = make_subplots(rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        for i, day in enumerate(day_order):
            day_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                day_values.append(match['avg_arrivals_by_day'].get(day, 0) if match else 0)
            fig2.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois], y=day_values, name=day,
                marker_color=day_color_map[day], opacity=0.8,
                text=[f'{v:.0f}' for v in day_values], textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=day,
            ), row=row_idx, col=1)
        fig2.add_hline(y=DAILY_SKU_CAPACITY, line_dash='dash', line_color='red',
                       line_width=2, annotation_text=f'Capacity ({DAILY_SKU_CAPACITY})',
                       annotation_position='top right', row=row_idx, col=1)
        fig2.update_yaxes(title_text='Average Unique SKUs Arrived', range=[0, y_max_avg], row=row_idx, col=1)
        fig2.update_xaxes(title_text='Target DOI', row=row_idx, col=1)

    fig2.update_layout(barmode='group',
        title_text='Average SKU Arrivals by Target DOI — Grouped by Reorder Threshold',
        title_font_size=16, height=500 * num_thresholds, autosize=True, legend_title_text='Day of Week')
    fig2.write_json(os.path.join(OUTPUT_DIR, f'comparison_avg_arrivals_bydoi_grouped_by_rt_{run_id}.json'))
    fig2.write_html(os.path.join(OUTPUT_DIR, f'comparison_avg_arrivals_bydoi_grouped_by_rt_{run_id}.html'))
    print("  ✓ Chart 2: Avg Arrivals by DOI (grouped by RT)")

    # ========================================
    # CHART 3: Binning Distribution by DOI — Grouped by RT
    # ========================================
    fig3 = make_subplots(rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        for bin_idx, bl in enumerate(bin_labels):
            bin_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                bin_values.append(int(match['bin_distribution'].get(bl, 0)) if match else 0)
            fig3.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois], y=bin_values, name=bl,
                marker_color=bin_color_map[bl], opacity=0.8, text=bin_values,
                textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=bl,
            ), row=row_idx, col=1)
        fig3.update_yaxes(title_text='Number of Days', range=[0, y_max_bin], row=row_idx, col=1)
        fig3.update_xaxes(title_text='Target DOI', row=row_idx, col=1)

    fig3.update_layout(barmode='group',
        title_text='Daily Arrivals Distribution by DOI — Grouped by Reorder Threshold',
        title_font_size=16, height=500 * num_thresholds, autosize=True, legend_title_text='Arrivals Range')
    fig3.write_json(os.path.join(OUTPUT_DIR, f'comparison_binning_distribution_byscenario_{run_id}.json'))
    fig3.write_html(os.path.join(OUTPUT_DIR, f'comparison_binning_distribution_byscenario_{run_id}.html'))
    print("  ✓ Chart 3: Binning Distribution by DOI (grouped by RT)")

    # ========================================
    # CHART 4: Avg Arrivals by RT — Grouped by DOI
    # ========================================
    fig4 = make_subplots(rows=num_dois, cols=1,
        subplot_titles=[f'Target DOI: {doi}' for doi in target_dois],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, doi in enumerate(target_dois, 1):
        doi_scenarios = [r for r in all_scenario_results if r['target_doi'] == doi]
        for i, day in enumerate(day_order):
            day_values = []
            for rt in reorder_thresholds:
                match = next((r for r in doi_scenarios if r['reorder_threshold'] == rt), None)
                day_values.append(match['avg_arrivals_by_day'].get(day, 0) if match else 0)
            fig4.add_trace(go.Bar(
                x=[f'RT {rt}' for rt in reorder_thresholds], y=day_values, name=day,
                marker_color=day_color_map[day], opacity=0.8,
                text=[f'{v:.0f}' for v in day_values], textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=day,
            ), row=row_idx, col=1)
        fig4.add_hline(y=DAILY_SKU_CAPACITY, line_dash='dash', line_color='red',
                       line_width=2, annotation_text=f'Capacity ({DAILY_SKU_CAPACITY})',
                       annotation_position='top right', row=row_idx, col=1)
        fig4.update_yaxes(title_text='Average Unique SKUs Arrived', range=[0, y_max_avg], row=row_idx, col=1)
        fig4.update_xaxes(title_text='Reorder Threshold', row=row_idx, col=1)

    fig4.update_layout(barmode='group',
        title_text='Average SKU Arrivals by Reorder Threshold — Grouped by Target DOI',
        title_font_size=16, height=500 * num_dois, autosize=True, legend_title_text='Day of Week')
    fig4.write_json(os.path.join(OUTPUT_DIR, f'comparison_avg_arrivals_byrt_grouped_by_doi_{run_id}.json'))
    fig4.write_html(os.path.join(OUTPUT_DIR, f'comparison_avg_arrivals_byrt_grouped_by_doi_{run_id}.html'))
    print("  ✓ Chart 4: Avg Arrivals by RT (grouped by DOI)")

    # ========================================
    # CHART 5: Overload Days by RT — Grouped by DOI
    # ========================================
    fig5 = make_subplots(rows=num_dois, cols=1,
        subplot_titles=[f'Target DOI: {doi}' for doi in target_dois],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, doi in enumerate(target_dois, 1):
        doi_scenarios = [r for r in all_scenario_results if r['target_doi'] == doi]
        for i, day in enumerate(day_order):
            day_values = []
            for rt in reorder_thresholds:
                match = next((r for r in doi_scenarios if r['reorder_threshold'] == rt), None)
                day_values.append(int(match['overload_by_day'].get(day, 0)) if match else 0)
            fig5.add_trace(go.Bar(
                x=[f'RT {rt}' for rt in reorder_thresholds], y=day_values, name=day,
                marker_color=day_color_map[day], opacity=0.8, text=day_values,
                textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=day,
            ), row=row_idx, col=1)
        fig5.update_yaxes(title_text='Number of Overload Days', range=[0, y_max_overload], row=row_idx, col=1)
        fig5.update_xaxes(title_text='Reorder Threshold', row=row_idx, col=1)

    fig5.update_layout(barmode='group',
        title_text=f'Overload Days by Reorder Threshold — Grouped by Target DOI<br><sup>(Days Exceeding {DAILY_SKU_CAPACITY} SKU Capacity)</sup>',
        title_font_size=16, height=500 * num_dois, autosize=True, legend_title_text='Day of Week')
    fig5.write_json(os.path.join(OUTPUT_DIR, f'comparison_overload_days_by_rt_grouped_by_doi_{run_id}.json'))
    fig5.write_html(os.path.join(OUTPUT_DIR, f'comparison_overload_days_by_rt_grouped_by_doi_{run_id}.html'))
    print("  ✓ Chart 5: Overload Days by RT (grouped by DOI)")

    # ========================================
    # CHART 6: Binning Distribution by RT — Grouped by DOI
    # ========================================
    fig6 = make_subplots(rows=num_dois, cols=1,
        subplot_titles=[f'Target DOI: {doi}' for doi in target_dois],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, doi in enumerate(target_dois, 1):
        doi_scenarios = [r for r in all_scenario_results if r['target_doi'] == doi]
        for bin_idx, bl in enumerate(bin_labels):
            bin_values = []
            for rt in reorder_thresholds:
                match = next((r for r in doi_scenarios if r['reorder_threshold'] == rt), None)
                bin_values.append(int(match['bin_distribution'].get(bl, 0)) if match else 0)
            fig6.add_trace(go.Bar(
                x=[f'RT {rt}' for rt in reorder_thresholds], y=bin_values, name=bl,
                marker_color=bin_color_map[bl], opacity=0.8, text=bin_values,
                textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=bl,
            ), row=row_idx, col=1)
        fig6.update_yaxes(title_text='Number of Days', range=[0, y_max_bin], row=row_idx, col=1)
        fig6.update_xaxes(title_text='Reorder Threshold', row=row_idx, col=1)

    fig6.update_layout(barmode='group',
        title_text='Daily Arrivals Distribution by Reorder Threshold — Grouped by Target DOI',
        title_font_size=16, height=500 * num_dois, autosize=True, legend_title_text='Arrivals Range')
    fig6.write_json(os.path.join(OUTPUT_DIR, f'comparison_binning_distribution_by_rt_grouped_by_doi_{run_id}.json'))
    fig6.write_html(os.path.join(OUTPUT_DIR, f'comparison_binning_distribution_by_rt_grouped_by_doi_{run_id}.html'))
    print("  ✓ Chart 6: Binning Distribution by RT (grouped by DOI)")

    # ========================================
    # CHART 7: Boxplot of Daily Arrivals — Grouped by RT
    # ========================================
    fig7 = make_subplots(rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        for doi in target_dois:
            match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
            if match:
                arrivals = match['daily_arrivals']
                filtered = arrivals[arrivals['day_of_week'] != 'Sunday']['unique_skus_arrived'].values
                fig7.add_trace(go.Box(
                    y=filtered, name=f'DOI {doi}', marker_color=doi_color_map[doi],
                    boxmean=True, showlegend=(row_idx == 1), legendgroup=f'DOI {doi}',
                ), row=row_idx, col=1)
        fig7.add_hline(y=DAILY_SKU_CAPACITY, line_dash='dash', line_color='red',
                       line_width=2, annotation_text=f'Daily Capacity ({DAILY_SKU_CAPACITY})',
                       annotation_position='top right', row=row_idx, col=1)
        fig7.update_yaxes(title_text='Daily Unique SKUs Arrived', range=[0, y_max_box], row=row_idx, col=1)
        fig7.update_xaxes(title_text='Target DOI', row=row_idx, col=1)

    fig7.update_layout(
        title_text='Distribution of Daily SKU Arrivals by Target DOI — Grouped by Reorder Threshold<br><sup>(Excluding Sundays)</sup>',
        title_font_size=16, height=500 * num_thresholds, autosize=True)
    fig7.write_json(os.path.join(OUTPUT_DIR, f'comparison_boxplot_arrivals_{run_id}.json'))
    fig7.write_html(os.path.join(OUTPUT_DIR, f'comparison_boxplot_arrivals_{run_id}.html'))
    print("  ✓ Chart 7: Boxplot of Daily Arrivals (grouped by RT)")

    # ========================================
    # CHART 8: Calendar Heatmap — one per scenario
    # ========================================
    bin_order = ['0-30', '31-90', '91-180', '181-270', '271-360', '361-540', '541-720', '720+', 'Sunday']
    bin_display_colors = {**bin_color_map, 'Sunday': '#eeeeee'}
    bins_edges = [0, 30, 90, 180, 270, 360, 540, 720, float('inf')]

    def get_bin_label(value, is_sunday=False):
        if is_sunday:
            return 'Sunday'
        for i in range(len(bins_edges) - 1):
            if bins_edges[i] < value <= bins_edges[i + 1]:
                return bin_labels[i]
        if value == 0:
            return '0-30'
        return '720+'

    day_abbr = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    for scenario in all_scenario_results:
        rt  = scenario['reorder_threshold']
        doi = scenario['target_doi']
        daily = scenario['daily_arrivals'].copy()
        daily['date'] = pd.to_datetime(daily['date'])
        min_date = daily['date'].min()
        start_monday = min_date - pd.Timedelta(days=min_date.weekday())
        daily['weekday']    = daily['date'].dt.weekday
        daily['week_num']   = ((daily['date'] - start_monday).dt.days // 7)
        daily['is_sunday']  = daily['weekday'] == 6
        daily['bin_label']  = daily.apply(
            lambda row: get_bin_label(row['unique_skus_arrived'], row['is_sunday']), axis=1)
        num_weeks = daily['week_num'].max() + 1

        fig_cal = go.Figure()
        for bin_label_val in bin_order:
            subset = daily[daily['bin_label'] == bin_label_val]
            if subset.empty:
                continue
            hover_texts = subset.apply(
                lambda r: (
                    f"<b>{r['date'].strftime('%a, %d %b %Y')}</b><br>"
                    f"Inbound SKUs: {int(r['unique_skus_arrived'])}<br>"
                    f"Inbound Qty: {int(r['inbound_quantity']):,}<br>"
                    f"Inbound Value: {r['inbound_value']:,.0f}<br>"
                    f"Bin: {r['bin_label']}"
                ), axis=1)
            fig_cal.add_trace(go.Scatter(
                x=subset['weekday'], y=subset['week_num'], mode='markers+text',
                name=bin_label_val,
                marker=dict(symbol='square', size=28,
                    color=bin_display_colors[bin_label_val],
                    line=dict(color='white', width=1)),
                text=subset['date'].dt.strftime('%-d'),
                textposition='middle center', textfont=dict(size=9, color='white'),
                hovertext=hover_texts, hoverinfo='text', legendgroup=bin_label_val,
            ))

        month_labels = []
        seen_months  = set()
        for _, row in daily.sort_values('date').iterrows():
            m_key = (row['date'].year, row['date'].month)
            if m_key not in seen_months:
                seen_months.add(m_key)
                month_labels.append(dict(x=-0.9, y=row['week_num'],
                    text=row['date'].strftime('%b %Y'), showarrow=False,
                    font=dict(size=10, color='#444'), xanchor='right'))

        fig_cal.update_layout(
            title_text=(f'Daily Inbound SKU Calendar — RT {rt} | DOI {doi}<br>'
                f'<sup>Each cell = one day, colored by arrival bin (Sundays excluded from binning)</sup>'),
            title_font_size=15, autosize=True, height=max(400, 60 * num_weeks + 120),
            xaxis=dict(tickmode='array', tickvals=list(range(7)), ticktext=day_abbr,
                       side='top', showgrid=False, zeroline=False),
            yaxis=dict(autorange='reversed', showticklabels=False, showgrid=False, zeroline=False),
            annotations=month_labels, plot_bgcolor='#f9f9f9',
            legend_title_text='Arrivals Bin', margin=dict(l=80, r=20, t=100, b=20),
            template='plotly_white')
        fig_cal.write_json(os.path.join(OUTPUT_DIR, f'calendar_inbound_RT{rt}_DOI{doi}_{run_id}.json'))
        fig_cal.write_html(os.path.join(OUTPUT_DIR, f'calendar_inbound_RT{rt}_DOI{doi}_{run_id}.html'))
        print(f"  ✓ Calendar chart: RT {rt} DOI {doi}")

    # ========================================
    # NEW CHART 9: Avg Daily Inbound Volume (Quantity) by DOI — Grouped by RT
    # ========================================
    fig9 = make_subplots(rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True, vertical_spacing=0.08)

    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        for i, day in enumerate(day_order):
            day_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                day_values.append(match['avg_volume_by_day'].get(day, 0) if match else 0)
            fig9.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois], y=day_values, name=day,
                marker_color=day_color_map[day], opacity=0.8,
                text=[f'{v:,.0f}' for v in day_values], textposition='outside', textfont_size=9,
                showlegend=(row_idx == 1), legendgroup=day,
            ), row=row_idx, col=1)
        fig9.update_yaxes(title_text='Avg Inbound Quantity', range=[0, y_max_vol], row=row_idx, col=1)
        fig9.update_xaxes(title_text='Target DOI', row=row_idx, col=1)

    fig9.update_layout(barmode='group',
        title_text='Average Daily Inbound Volume (Quantity) by DOI — Grouped by RT<br><sup>(Total units arriving per day, NOT unique SKUs)</sup>',
        title_font_size=16, height=500 * num_thresholds, autosize=True, legend_title_text='Day of Week')
    fig9.write_json(os.path.join(OUTPUT_DIR, f'comparison_avg_volume_bydoi_grouped_by_rt_{run_id}.json'))
    fig9.write_html(os.path.join(OUTPUT_DIR, f'comparison_avg_volume_bydoi_grouped_by_rt_{run_id}.html'))
    print("  ✓ Chart 9: Avg Daily Inbound Volume by DOI (grouped by RT)")

    # ========================================
    # NEW CHART 10: Avg Daily Inbound Value by DOI — Grouped by RT
    # ========================================
    if has_price:
        fig10 = make_subplots(rows=num_thresholds, cols=1,
            subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
            shared_yaxes=True, vertical_spacing=0.08)

        for row_idx, rt in enumerate(reorder_thresholds, 1):
            rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
            for i, day in enumerate(day_order):
                day_values = []
                for doi in target_dois:
                    match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                    day_values.append(match['avg_value_by_day'].get(day, 0) if match else 0)
                fig10.add_trace(go.Bar(
                    x=[f'DOI {doi}' for doi in target_dois], y=day_values, name=day,
                    marker_color=day_color_map[day], opacity=0.8,
                    text=[f'{v:,.0f}' for v in day_values], textposition='outside', textfont_size=9,
                    showlegend=(row_idx == 1), legendgroup=day,
                ), row=row_idx, col=1)
            fig10.update_yaxes(title_text='Avg Inbound Value', range=[0, y_max_val], row=row_idx, col=1)
            fig10.update_xaxes(title_text='Target DOI', row=row_idx, col=1)

        fig10.update_layout(barmode='group',
            title_text='Average Daily Inbound Value (net_price × qty) by DOI — Grouped by RT<br><sup>(Monetary value of arriving orders per day)</sup>',
            title_font_size=16, height=500 * num_thresholds, autosize=True, legend_title_text='Day of Week')
        fig10.write_json(os.path.join(OUTPUT_DIR, f'comparison_avg_value_bydoi_grouped_by_rt_{run_id}.json'))
        fig10.write_html(os.path.join(OUTPUT_DIR, f'comparison_avg_value_bydoi_grouped_by_rt_{run_id}.html'))
        print("  ✓ Chart 10: Avg Daily Inbound Value by DOI (grouped by RT)")
    else:
        print("  ⚠ Chart 10 skipped — no net_price data")

    # ========================================
    # SUMMARY
    # ========================================
    print("\n" + "="*60)
    print("MULTI-SCENARIO ANALYSIS COMPLETE!")
    print(f"Volume tracking: ✓ enabled")
    print(f"Value tracking:  {'✓ enabled' if has_price else '✗ disabled (no net_price column)'}")


if __name__ == "__main__":
    main()
