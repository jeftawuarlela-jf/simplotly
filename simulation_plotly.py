import pandas as pd
import numpy as np
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
    # Default configuration
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

# Convert date tuples to datetime objects if needed
if isinstance(START_DATE, tuple):
    START_DATE = datetime(*START_DATE)
if isinstance(END_DATE, tuple):
    END_DATE = datetime(*END_DATE)

# ========================================
# HELPER FUNCTIONS
# ========================================
def add_working_days(start_date, working_days):
    """
    Add working days to a date, skipping weekends.
    
    Args:
        start_date: Starting date
        working_days: Number of working days to add
    
    Returns:
        Date after adding working days
    """
    current_date = start_date
    days_added = 0
    
    while days_added < working_days:
        current_date += timedelta(days=1)
        # Check if it's a weekday (Monday=0, Sunday=6)
        if current_date.weekday() < 6:  # Monday to Friday
            days_added += 1
    
    return current_date

def run_single_simulation(sku_info, reorder_threshold, target_doi, date_range):
    """
    Run a single simulation with given parameters.
    
    Args:
        sku_info: DataFrame with SKU information
        reorder_threshold: Reorder threshold value
        target_doi: Target DOI value
        date_range: Date range for simulation
    
    Returns:
        DataFrame with simulation results
    """
    results = []
    
    for idx, sku_row in sku_info.iterrows():
        sku_code = sku_row['sku_code']
        product_name = sku_row['product_name']
        stock = sku_row['stock']
        qpd = sku_row['qpd']
        lead_time_days = int(sku_row['lead_time_days'])
        
        # Skip SKUs with no sales
        if qpd == 0 or pd.isna(qpd):
            continue
        
        # Track orders in transit: list of (arrival_date, quantity)
        orders_in_transit = []
        
        # Simulate each day
        for date in date_range:
            stock_beginning = stock
            
            # Check for arriving orders today
            arriving_orders = [order for order in orders_in_transit if order[0] == date]
            stock_received = sum([order[1] for order in arriving_orders])
            stock += stock_received
            
            # Remove received orders from transit list
            orders_in_transit = [order for order in orders_in_transit if order[0] != date]
            
            # Daily sales
            sales = qpd
            stock -= sales
            
            # Calculate DOI
            doi = stock / qpd if qpd > 0 else 999
            
            # Calculate total orders in transit
            total_in_transit = sum([order[1] for order in orders_in_transit])
            
            # Check if we need to reorder
            reorder_trigger = (doi <= reorder_threshold) and (len(orders_in_transit) == 0)
            
            order_placed = False
            order_quantity = 0
            
            if reorder_trigger:
                # Calculate order quantity to reach target DOI after lead time
                estimated_calendar_days = lead_time_days * 1.17
                order_quantity = (target_doi + estimated_calendar_days) * qpd - stock
                
                # Only place order if quantity is positive
                if order_quantity > 0:
                    order_placed = True
                    arrival_date = add_working_days(date, lead_time_days)
                    orders_in_transit.append((arrival_date, order_quantity))
            
            # Store daily results
            results.append({
                'date': date,
                'sku_code': sku_code,
                'product_name': product_name,
                'lead_time_days': lead_time_days,
                'stock_beginning': stock_beginning,
                'sales': sales,
                'stock_received': stock_received,
                'stock_ending': stock,
                'doi': doi,
                'order_placed': order_placed,
                'order_quantity': order_quantity,
                'orders_in_transit_qty': total_in_transit,
                'orders_in_transit_count': len(orders_in_transit)
            })
    
    return pd.DataFrame(results)

def analyze_simulation(results_df, reorder_threshold, target_doi, date_range):
    """
    Analyze simulation results and return key metrics.
    
    Args:
        results_df: DataFrame with simulation results
        reorder_threshold: Reorder threshold used
        target_doi: Target DOI used
        date_range: Date range of simulation
    
    Returns:
        Dictionary with analysis metrics
    """
    # Count unique SKUs that ARRIVED (received) each day
    daily_arrivals = results_df[results_df['stock_received'] > 0].groupby('date').agg({
        'sku_code': 'count'
    }).reset_index()
    daily_arrivals.columns = ['date', 'unique_skus_arrived']
    
    # Create complete date range (including days with 0 arrivals)
    all_dates = pd.DataFrame({'date': date_range})
    daily_arrivals = all_dates.merge(daily_arrivals, on='date', how='left').fillna(0)
    
    # Add day of week column
    daily_arrivals['day_of_week'] = daily_arrivals['date'].dt.day_name()
    
    # Calculate statistics
    avg_daily_skus = daily_arrivals['unique_skus_arrived'].mean()
    max_daily_skus = daily_arrivals['unique_skus_arrived'].max()
    median_daily_skus = daily_arrivals['unique_skus_arrived'].median()
    std_daily_skus = daily_arrivals['unique_skus_arrived'].std()
    
    # Days exceeding capacity
    days_over_capacity = (daily_arrivals['unique_skus_arrived'] > DAILY_SKU_CAPACITY).sum()
    
    # Binning analysis - categorize daily arrivals into ranges (EXCLUDING SUNDAYS)
    bins = [0, 30, 90, 180, 270, 360, 540, 720, float('inf')]
    bin_labels = ['0-30', '31-90', '91-180', '181-270', '271-360', '361-540', '541-720', '720+']
    
    # Filter out Sundays for binning analysis
    daily_arrivals_no_sunday = daily_arrivals[daily_arrivals['day_of_week'] != 'Sunday'].copy()
    
    daily_arrivals_no_sunday['bin'] = pd.cut(daily_arrivals_no_sunday['unique_skus_arrived'], 
                                              bins=bins, 
                                              labels=bin_labels, 
                                              include_lowest=True)
    
    # Count days in each bin (excluding Sundays)
    bin_counts = daily_arrivals_no_sunday['bin'].value_counts().sort_index()
    bin_distribution = dict(zip(bin_labels, [bin_counts.get(label, 0) for label in bin_labels]))
    
    # Total unique SKUs that arrived over the period
    total_unique_skus_arrived = results_df[results_df['stock_received'] > 0]['sku_code'].nunique()
    
    # Calculate average DOI
    avg_doi = results_df['doi'].mean()
    
    # Total orders placed
    total_orders = results_df['order_placed'].sum()
    
    # Calculate overload days by day of week
    daily_arrivals['is_overload'] = daily_arrivals['unique_skus_arrived'] > DAILY_SKU_CAPACITY
    overload_by_day = daily_arrivals.groupby('day_of_week')['is_overload'].sum().to_dict()
    
    # Calculate average arrivals by day of week
    avg_arrivals_by_day = daily_arrivals.groupby('day_of_week')['unique_skus_arrived'].mean().to_dict()
    
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
        'bin_distribution': bin_distribution
    }

# ========================================
# MAIN EXECUTION
# ========================================
def main():
    # Generate a unique run ID based on current datetime
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"Run ID: {run_id}")
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load data
    print("Loading data...")
    df = pd.read_csv(DATA_FILE)
    df['tanggal_update'] = pd.to_datetime(df['tanggal_update'])
    
    
    # Prepare starting inventory
    print("\nPreparing starting inventory (July 1, 2025)...")
    july_1 = datetime(2025, 7, 1)
    starting_data = df[df['tanggal_update'] == july_1].copy()
    
    if len(starting_data) == 0:
        print(f"Warning: No data for July 1, using first available date: {df['tanggal_update'].min()}")
        starting_data = df[df['tanggal_update'] == df['tanggal_update'].min()].copy()
    
    sku_info = starting_data.groupby('sku_code').agg({
        'product_name': 'first',
        'stock': 'first',
        'qpd': 'first',
        'doi': 'first',
        'lead_time_days': 'first'
    }).reset_index()
    
    print(f"Starting with {len(sku_info)} unique SKUs")
    print(f"Lead time range: {sku_info['lead_time_days'].min():.0f} to {sku_info['lead_time_days'].max():.0f} working days")
    
    # Generate date range
    date_range = pd.date_range(START_DATE, END_DATE, freq='D')
    
    # Generate all parameter combinations
    param_combinations = list(product(REORDER_THRESHOLD_RANGE, TARGET_DOI_RANGE))
    total_scenarios = len(param_combinations)
    
    
    # Store all results
    all_scenario_results = []
    
    # Define day order for proper sorting
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    
    # Run simulations for each combination
    for scenario_num, (reorder_threshold, target_doi) in enumerate(param_combinations, 1):
        print(f"\nScenario {scenario_num}/{total_scenarios}: Reorder Threshold={reorder_threshold}, Target DOI={target_doi}")
        
        
        # Run simulation
        results_df = run_single_simulation(sku_info, reorder_threshold, target_doi, date_range)
        
        # Analyze results
        analysis = analyze_simulation(results_df, reorder_threshold, target_doi, date_range)
        all_scenario_results.append(analysis)
        
    # Save detailed results for this scenario
        if SAVE_DETAILED_RESULTS:
            scenario_filename = f"scenario_RT{reorder_threshold}_DOI{target_doi}_detailed2.csv"
            results_df.to_csv(os.path.join(OUTPUT_DIR, scenario_filename), index=False)
            print(f"\n  ✓ Saved: {scenario_filename}")
        
    # Save daily arrivals for this scenario
        if SAVE_DAILY_SUMMARIES:
            daily_filename = f"scenario_RT{reorder_threshold}_DOI{target_doi}_daily2.csv"
            analysis['daily_arrivals'].to_csv(os.path.join(OUTPUT_DIR, daily_filename), index=False)
        
       
    # Create comparison summary
    print(f"\n{'='*60}")
    print("CREATING COMPARISON SUMMARY")
    print(f"{'='*60}")
    
    comparison_df = pd.DataFrame([
        {
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
            # Add overload days by day of week
            'Overload_Monday': int(r['overload_by_day'].get('Monday', 0)),
            'Overload_Tuesday': int(r['overload_by_day'].get('Tuesday', 0)),
            'Overload_Wednesday': int(r['overload_by_day'].get('Wednesday', 0)),
            'Overload_Thursday': int(r['overload_by_day'].get('Thursday', 0)),
            'Overload_Friday': int(r['overload_by_day'].get('Friday', 0)),
            'Overload_Saturday': int(r['overload_by_day'].get('Saturday', 0)),
            'Overload_Sunday': int(r['overload_by_day'].get('Sunday', 0)),
            # Add average arrivals by day of week
            'Avg_Monday': round(r['avg_arrivals_by_day'].get('Monday', 0), 2),
            'Avg_Tuesday': round(r['avg_arrivals_by_day'].get('Tuesday', 0), 2),
            'Avg_Wednesday': round(r['avg_arrivals_by_day'].get('Wednesday', 0), 2),
            'Avg_Thursday': round(r['avg_arrivals_by_day'].get('Thursday', 0), 2),
            'Avg_Friday': round(r['avg_arrivals_by_day'].get('Friday', 0), 2),
            'Avg_Saturday': round(r['avg_arrivals_by_day'].get('Saturday', 0), 2),
            'Avg_Sunday': round(r['avg_arrivals_by_day'].get('Sunday', 0), 2)
        }
        for r in all_scenario_results
    ])
    
    # Sort by multiple criteria for better analysis
    comparison_df = comparison_df.sort_values(['Reorder_Threshold', 'Target_DOI'])
    
    # Save comparison summary
    comparison_df.to_csv(os.path.join(OUTPUT_DIR, f'scenario_comparison_summary_byday_{run_id}.csv'), index=False)
    
    # Display comparison table
    print("\n" + "="*60)
    print("SCENARIO COMPARISON TABLE")
    print("="*60)
    print(comparison_df.to_string(index=False))
    
    # Find optimal scenarios
    print("\n" + "="*60)
    print("OPTIMAL SCENARIO ANALYSIS")
    print("="*60)
    
    # Best scenario for minimizing capacity overload
    best_capacity = comparison_df.loc[comparison_df['Days_Over_Capacity'].idxmin()]
    print(f"\n✓ Best for capacity (fewest days over limit):")
    print(f"  Scenario: {best_capacity['Scenario']}")
    print(f"  Days over capacity: {best_capacity['Days_Over_Capacity']}")
    print(f"  Capacity utilization: {best_capacity['Capacity_Utilization_Pct']:.1f}%")
    
    
    # ========================================
    # SHARED SETUP FOR ALL CHARTS (Plotly)
    # ========================================
    print("\n" + "="*60)
    print("CREATING ALL VISUALIZATIONS (Plotly Interactive Charts)")
    print("="*60)
    
    # Extract unique reorder thresholds and target DOIs
    reorder_thresholds = sorted(set(r['reorder_threshold'] for r in all_scenario_results))
    target_dois = sorted(set(r['target_doi'] for r in all_scenario_results))
    num_thresholds = len(reorder_thresholds)
    num_scenarios = len(all_scenario_results)
    
    # Color palettes
    day_colors_list = px.colors.qualitative.Set2[:len(day_order)]
    day_color_map = {day: day_colors_list[i] for i, day in enumerate(day_order)}
    
    doi_colors_list = px.colors.qualitative.Set2[:len(target_dois)]
    doi_color_map = {doi: doi_colors_list[i] for i, doi in enumerate(target_dois)}
    
    bin_labels = ['0-30', '31-90', '91-180', '181-270', '271-360', '361-540', '541-720', '720+']
    bin_colors_list = px.colors.qualitative.T10[:len(bin_labels)]
    bin_color_map = {bl: bin_colors_list[i] for i, bl in enumerate(bin_labels)}
    
    # Global y-max values for consistent scaling
    all_avg_values = [r['avg_arrivals_by_day'].get(d, 0) for r in all_scenario_results for d in day_order]
    y_max_avg = max(all_avg_values) * 1.20
    
    all_overload_values = [int(r['overload_by_day'].get(d, 0)) for r in all_scenario_results for d in day_order]
    y_max_overload = max(all_overload_values) * 1.20 if max(all_overload_values) > 0 else 10
    
    all_bin_values = [int(r['bin_distribution'].get(bl, 0)) for r in all_scenario_results for bl in bin_labels]
    y_max_bin = max(all_bin_values) * 1.20 if max(all_bin_values) > 0 else 10
    
    # Global y-max for boxplot (daily arrivals excluding Sundays)
    all_box_values = []
    for r in all_scenario_results:
        arrivals = r['daily_arrivals']
        filtered = arrivals[arrivals['day_of_week'] != 'Sunday']['unique_skus_arrived'].values
        if len(filtered) > 0:
            all_box_values.append(filtered.max())
    y_max_box = max(all_box_values) * 1.15 if all_box_values else 1000

    num_dois = len(target_dois)
    
    # ========================================
    # CHART 1: Overload Days by DOI — Grouped by RT
    # ========================================
    
    fig1 = make_subplots(
        rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        
        for i, day in enumerate(day_order):
            day_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                day_values.append(int(match['overload_by_day'].get(day, 0)) if match else 0)
            
            fig1.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois],
                y=day_values,
                name=day,
                marker_color=day_color_map[day],
                opacity=0.8,
                text=day_values,
                textposition='outside',
                textfont_size=9,
                showlegend=(row_idx == 1),  # Only show legend once
                legendgroup=day,
            ), row=row_idx, col=1)
        
        fig1.update_yaxes(title_text='Number of Overload Days', range=[0, y_max_overload], row=row_idx, col=1)
        fig1.update_xaxes(title_text='Target DOI', row=row_idx, col=1)
    
    fig1.update_layout(
        barmode='group',
        title_text=f'Overload Days by Target DOI — Grouped by Reorder Threshold<br><sup>(Days Exceeding {DAILY_SKU_CAPACITY} SKU Capacity)</sup>',
        title_font_size=16,
        height=500 * num_thresholds,
        autosize=False, width=900,
        legend_title_text='Day of Week',
    )
    fig1.write_html(os.path.join(OUTPUT_DIR, f'comparison_overload_days_bydoi_grouped_by_rt_{run_id}.html'))
    print("  ✓ Chart 1: Overload Days by DOI (grouped by RT)")
    
    # ========================================
    # CHART 2: Avg Arrivals by DOI — Grouped by RT
    # ========================================
    
    fig2 = make_subplots(
        rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        
        for i, day in enumerate(day_order):
            day_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                day_values.append(match['avg_arrivals_by_day'].get(day, 0) if match else 0)
            
            fig2.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois],
                y=day_values,
                name=day,
                marker_color=day_color_map[day],
                opacity=0.8,
                text=[f'{v:.0f}' for v in day_values],
                textposition='outside',
                textfont_size=9,
                showlegend=(row_idx == 1),
                legendgroup=day,
            ), row=row_idx, col=1)
        
        # Add capacity line
        fig2.add_hline(y=DAILY_SKU_CAPACITY, line_dash='dash', line_color='red',
                       line_width=2, annotation_text=f'Capacity ({DAILY_SKU_CAPACITY})',
                       annotation_position='top right', row=row_idx, col=1)
        
        fig2.update_yaxes(title_text='Average Unique SKUs Arrived', range=[0, y_max_avg], row=row_idx, col=1)
        fig2.update_xaxes(title_text='Target DOI', row=row_idx, col=1)
    
    fig2.update_layout(
        barmode='group',
        title_text='Average SKU Arrivals by Target DOI — Grouped by Reorder Threshold',
        title_font_size=16,
        height=500 * num_thresholds,
        autosize=False, width=900,
        legend_title_text='Day of Week',
    )
    fig2.write_html(os.path.join(OUTPUT_DIR, f'comparison_avg_arrivals_bydoi_grouped_by_rt_{run_id}.html'))
    print("  ✓ Chart 2: Avg Arrivals by DOI (grouped by RT)")
    
    # ========================================
    # CHART 3: Binning Distribution by DOI — Grouped by RT
    # ========================================
    
    fig3 = make_subplots(
        rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        
        for bin_idx, bl in enumerate(bin_labels):
            bin_values = []
            for doi in target_dois:
                match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
                bin_values.append(int(match['bin_distribution'].get(bl, 0)) if match else 0)
            
            fig3.add_trace(go.Bar(
                x=[f'DOI {doi}' for doi in target_dois],
                y=bin_values,
                name=bl,
                marker_color=bin_color_map[bl],
                opacity=0.8,
                text=bin_values,
                textposition='outside',
                textfont_size=9,
                showlegend=(row_idx == 1),
                legendgroup=bl,
            ), row=row_idx, col=1)
        
        fig3.update_yaxes(title_text='Number of Days', range=[0, y_max_bin], row=row_idx, col=1)
        fig3.update_xaxes(title_text='Target DOI', row=row_idx, col=1)
    
    fig3.update_layout(
        barmode='group',
        title_text='Daily Arrivals Distribution by DOI — Grouped by Reorder Threshold',
        title_font_size=16,
        height=500 * num_thresholds,
        autosize=False, width=900,
        legend_title_text='Arrivals Range',
    )
    fig3.write_html(os.path.join(OUTPUT_DIR, f'comparison_binning_distribution_byscenario_{run_id}.html'))
    print("  ✓ Chart 3: Binning Distribution by DOI (grouped by RT)")
    
    # ========================================
    # CHART 4: Avg Arrivals by RT — Grouped by DOI
    # ========================================
    
    fig4 = make_subplots(
        rows=num_dois, cols=1,
        subplot_titles=[f'Target DOI: {doi}' for doi in target_dois],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, doi in enumerate(target_dois, 1):
        doi_scenarios = [r for r in all_scenario_results if r['target_doi'] == doi]
        
        for i, day in enumerate(day_order):
            day_values = []
            for rt in reorder_thresholds:
                match = next((r for r in doi_scenarios if r['reorder_threshold'] == rt), None)
                day_values.append(match['avg_arrivals_by_day'].get(day, 0) if match else 0)
            
            fig4.add_trace(go.Bar(
                x=[f'RT {rt}' for rt in reorder_thresholds],
                y=day_values,
                name=day,
                marker_color=day_color_map[day],
                opacity=0.8,
                text=[f'{v:.0f}' for v in day_values],
                textposition='outside',
                textfont_size=9,
                showlegend=(row_idx == 1),
                legendgroup=day,
            ), row=row_idx, col=1)
        
        # Add capacity line
        fig4.add_hline(y=DAILY_SKU_CAPACITY, line_dash='dash', line_color='red',
                       line_width=2, annotation_text=f'Capacity ({DAILY_SKU_CAPACITY})',
                       annotation_position='top right', row=row_idx, col=1)
        
        fig4.update_yaxes(title_text='Average Unique SKUs Arrived', range=[0, y_max_avg], row=row_idx, col=1)
        fig4.update_xaxes(title_text='Reorder Threshold', row=row_idx, col=1)
    
    fig4.update_layout(
        barmode='group',
        title_text='Average SKU Arrivals by Reorder Threshold — Grouped by Target DOI',
        title_font_size=16,
        height=500 * num_dois,
        autosize=False, width=900,
        legend_title_text='Day of Week',
    )
    fig4.write_html(os.path.join(OUTPUT_DIR, f'comparison_avg_arrivals_byrt_grouped_by_doi_{run_id}.html'))
    print("  ✓ Chart 4: Avg Arrivals by RT (grouped by DOI)")
    
    # ========================================
    # CHART 5: Overload Days by RT — Grouped by DOI
    # ========================================
    
    fig5 = make_subplots(
        rows=num_dois, cols=1,
        subplot_titles=[f'Target DOI: {doi}' for doi in target_dois],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, doi in enumerate(target_dois, 1):
        doi_scenarios = [r for r in all_scenario_results if r['target_doi'] == doi]
        
        for i, day in enumerate(day_order):
            day_values = []
            for rt in reorder_thresholds:
                match = next((r for r in doi_scenarios if r['reorder_threshold'] == rt), None)
                day_values.append(int(match['overload_by_day'].get(day, 0)) if match else 0)
            
            fig5.add_trace(go.Bar(
                x=[f'RT {rt}' for rt in reorder_thresholds],
                y=day_values,
                name=day,
                marker_color=day_color_map[day],
                opacity=0.8,
                text=day_values,
                textposition='outside',
                textfont_size=9,
                showlegend=(row_idx == 1),
                legendgroup=day,
            ), row=row_idx, col=1)
        
        fig5.update_yaxes(title_text='Number of Overload Days', range=[0, y_max_overload], row=row_idx, col=1)
        fig5.update_xaxes(title_text='Reorder Threshold', row=row_idx, col=1)
    
    fig5.update_layout(
        barmode='group',
        title_text=f'Overload Days by Reorder Threshold — Grouped by Target DOI<br><sup>(Days Exceeding {DAILY_SKU_CAPACITY} SKU Capacity)</sup>',
        title_font_size=16,
        height=500 * num_dois,
        autosize=False, width=900,
        legend_title_text='Day of Week',
    )
    fig5.write_html(os.path.join(OUTPUT_DIR, f'comparison_overload_days_by_rt_grouped_by_doi_{run_id}.html'))
    print("  ✓ Chart 5: Overload Days by RT (grouped by DOI)")
    
    # ========================================
    # CHART 6: Binning Distribution by RT — Grouped by DOI
    # ========================================
    
    fig6 = make_subplots(
        rows=num_dois, cols=1,
        subplot_titles=[f'Target DOI: {doi}' for doi in target_dois],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, doi in enumerate(target_dois, 1):
        doi_scenarios = [r for r in all_scenario_results if r['target_doi'] == doi]
        
        for bin_idx, bl in enumerate(bin_labels):
            bin_values = []
            for rt in reorder_thresholds:
                match = next((r for r in doi_scenarios if r['reorder_threshold'] == rt), None)
                bin_values.append(int(match['bin_distribution'].get(bl, 0)) if match else 0)
            
            fig6.add_trace(go.Bar(
                x=[f'RT {rt}' for rt in reorder_thresholds],
                y=bin_values,
                name=bl,
                marker_color=bin_color_map[bl],
                opacity=0.8,
                text=bin_values,
                textposition='outside',
                textfont_size=9,
                showlegend=(row_idx == 1),
                legendgroup=bl,
            ), row=row_idx, col=1)
        
        fig6.update_yaxes(title_text='Number of Days', range=[0, y_max_bin], row=row_idx, col=1)
        fig6.update_xaxes(title_text='Reorder Threshold', row=row_idx, col=1)
    
    fig6.update_layout(
        barmode='group',
        title_text='Daily Arrivals Distribution by Reorder Threshold — Grouped by Target DOI',
        title_font_size=16,
        height=500 * num_dois,
        autosize=False, width=900,
        legend_title_text='Arrivals Range',
    )
    fig6.write_html(os.path.join(OUTPUT_DIR, f'comparison_binning_distribution_by_rt_grouped_by_doi_{run_id}.html'))
    print("  ✓ Chart 6: Binning Distribution by RT (grouped by DOI)")
    
    # ========================================
    # CHART 7: Boxplot of Daily Arrivals — Grouped by RT
    # ========================================
    
    fig7 = make_subplots(
        rows=num_thresholds, cols=1,
        subplot_titles=[f'Reorder Threshold: {rt}' for rt in reorder_thresholds],
        shared_yaxes=True,
        vertical_spacing=0.08
    )
    
    for row_idx, rt in enumerate(reorder_thresholds, 1):
        rt_scenarios = [r for r in all_scenario_results if r['reorder_threshold'] == rt]
        
        for doi in target_dois:
            match = next((r for r in rt_scenarios if r['target_doi'] == doi), None)
            if match:
                arrivals = match['daily_arrivals']
                filtered = arrivals[arrivals['day_of_week'] != 'Sunday']['unique_skus_arrived'].values
                
                fig7.add_trace(go.Box(
                    y=filtered,
                    name=f'DOI {doi}',
                    marker_color=doi_color_map[doi],
                    boxmean=True,
                    showlegend=(row_idx == 1),
                    legendgroup=f'DOI {doi}',
                ), row=row_idx, col=1)
        
        # Add capacity line
        fig7.add_hline(y=DAILY_SKU_CAPACITY, line_dash='dash', line_color='red',
                       line_width=2, annotation_text=f'Daily Capacity ({DAILY_SKU_CAPACITY})',
                       annotation_position='top right', row=row_idx, col=1)
        
        fig7.update_yaxes(title_text='Daily Unique SKUs Arrived', range=[0, y_max_box], row=row_idx, col=1)
        fig7.update_xaxes(title_text='Target DOI', row=row_idx, col=1)
    
    fig7.update_layout(
        title_text='Distribution of Daily SKU Arrivals by Target DOI — Grouped by Reorder Threshold<br><sup>(Excluding Sundays)</sup>',
        title_font_size=16,
        height=500 * num_thresholds,
        autosize=False, width=900,
    )
    fig7.write_html(os.path.join(OUTPUT_DIR, f'comparison_boxplot_arrivals_{run_id}.html'))
    print("  ✓ Chart 7: Boxplot of Daily Arrivals (grouped by RT)")
    
    # ========================================
    # SUMMARY
    # ========================================
    print("\n" + "="*60)
    print("MULTI-SCENARIO ANALYSIS COMPLETE!")
    

if __name__ == "__main__":
    main()
