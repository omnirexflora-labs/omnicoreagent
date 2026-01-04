#!/usr/bin/env python3
"""
AGENT #3: DATA ANALYTICS AGENT
Real-world production agent for business intelligence and data analysis
"""

import os
import asyncio
from datetime import datetime, timedelta
from omnicoreagent import (
    MemoryRouter,
    EventRouter,
    BackgroundAgentManager,
    ToolRegistry,
)

# Initialize tool registry
tool_registry = ToolRegistry()

# ============================================================================
# REAL-WORLD TOOLS FOR DATA ANALYTICS
# ============================================================================


@tool_registry.register_tool("query_sales_data")
def query_sales_data(days: int = 30) -> str:
    """Query sales data from the database."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/analytics.db")

        if not os.path.exists(db_path):
            return "No analytics database found. Run seed_analytics_data() first."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get sales data
        cursor.execute(
            """SELECT 
                            date(sale_date) as date,
                            COUNT(*) as transaction_count,
                            SUM(amount) as total_revenue,
                            AVG(amount) as avg_transaction,
                            MIN(amount) as min_sale,
                            MAX(amount) as max_sale
                          FROM sales
                          WHERE sale_date >= date('now', '-' || ? || ' days')
                          GROUP BY date(sale_date)
                          ORDER BY date(sale_date) DESC""",
            (days,),
        )

        results = cursor.fetchall()
        conn.close()

        if not results:
            return f"No sales data found for the last {days} days"

        report = f"Sales Data (Last {days} Days):\n\n"

        total_revenue = sum(r[2] for r in results)
        total_transactions = sum(r[1] for r in results)

        report += "SUMMARY:\n"
        report += f"  Total Revenue: ${total_revenue:,.2f}\n"
        report += f"  Total Transactions: {total_transactions}\n"
        report += f"  Average Daily Revenue: ${total_revenue / len(results):,.2f}\n"
        report += f"  Average Transaction Value: ${total_revenue / total_transactions:,.2f}\n\n"

        report += "DAILY BREAKDOWN (Last 7 Days):\n"
        for row in results[:7]:
            report += f"  {row[0]}: {row[1]} sales, ${row[2]:,.2f} revenue (avg ${row[3]:.2f})\n"

        return report

    except Exception as e:
        return f"Error querying sales data: {str(e)}"


@tool_registry.register_tool("analyze_customer_behavior")
def analyze_customer_behavior() -> str:
    """Analyze customer purchase patterns and behavior."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/analytics.db")

        if not os.path.exists(db_path):
            return "No analytics database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Customer segmentation
        cursor.execute("""SELECT 
                            customer_id,
                            COUNT(*) as purchase_count,
                            SUM(amount) as total_spent,
                            AVG(amount) as avg_order_value,
                            MAX(sale_date) as last_purchase
                          FROM sales
                          GROUP BY customer_id
                          ORDER BY total_spent DESC""")

        customers = cursor.fetchall()

        # Calculate segments
        if not customers:
            conn.close()
            return "No customer data available"

        high_value = [c for c in customers if c[2] > 1000]
        medium_value = [c for c in customers if 500 <= c[2] <= 1000]
        low_value = [c for c in customers if c[2] < 500]

        # Repeat customers
        repeat_customers = [c for c in customers if c[1] > 3]

        conn.close()

        report = "Customer Behavior Analysis:\n\n"
        report += "CUSTOMER SEGMENTATION:\n"
        report += f"  Total Unique Customers: {len(customers)}\n"
        report += f"  High Value (>$1000): {len(high_value)} customers\n"
        report += f"  Medium Value ($500-$1000): {len(medium_value)} customers\n"
        report += f"  Low Value (<$500): {len(low_value)} customers\n\n"

        report += "LOYALTY METRICS:\n"
        report += f"  Repeat Customers (>3 purchases): {len(repeat_customers)} ({len(repeat_customers) / len(customers) * 100:.1f}%)\n"
        report += f"  Average Purchases per Customer: {sum(c[1] for c in customers) / len(customers):.1f}\n\n"

        report += "TOP 5 CUSTOMERS:\n"
        for i, customer in enumerate(customers[:5], 1):
            report += f"  {i}. Customer #{customer[0]}: ${customer[2]:,.2f} total ({customer[1]} purchases)\n"

        return report

    except Exception as e:
        return f"Error analyzing customer behavior: {str(e)}"


@tool_registry.register_tool("analyze_product_performance")
def analyze_product_performance() -> str:
    """Analyze product sales performance."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/analytics.db")

        if not os.path.exists(db_path):
            return "No analytics database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Product performance
        cursor.execute("""SELECT 
                            product_id,
                            COUNT(*) as units_sold,
                            SUM(amount) as total_revenue,
                            AVG(amount) as avg_price
                          FROM sales
                          GROUP BY product_id
                          ORDER BY total_revenue DESC""")

        products = cursor.fetchall()
        conn.close()

        if not products:
            return "No product data available"

        total_revenue = sum(p[2] for p in products)
        total_units = sum(p[1] for p in products)

        report = "Product Performance Analysis:\n\n"
        report += "OVERVIEW:\n"
        report += f"  Total Products: {len(products)}\n"
        report += f"  Total Units Sold: {total_units}\n"
        report += f"  Total Revenue: ${total_revenue:,.2f}\n\n"

        report += "TOP PERFORMERS:\n"
        for i, product in enumerate(products[:5], 1):
            revenue_share = (product[2] / total_revenue) * 100
            report += f"  {i}. Product #{product[0]}: ${product[2]:,.2f} ({product[1]} units, {revenue_share:.1f}% of revenue)\n"

        report += "\nLOWEST PERFORMERS:\n"
        for i, product in enumerate(products[-3:], 1):
            report += f"  {i}. Product #{product[0]}: ${product[2]:,.2f} ({product[1]} units)\n"

        return report

    except Exception as e:
        return f"Error analyzing product performance: {str(e)}"


@tool_registry.register_tool("detect_trends")
def detect_trends(metric: str = "revenue") -> str:
    """Detect trends in business metrics."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/analytics.db")

        if not os.path.exists(db_path):
            return "No analytics database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get daily metrics for trend analysis
        cursor.execute("""SELECT 
                            date(sale_date) as date,
                            COUNT(*) as transactions,
                            SUM(amount) as revenue,
                            AVG(amount) as avg_transaction
                          FROM sales
                          WHERE sale_date >= date('now', '-30 days')
                          GROUP BY date(sale_date)
                          ORDER BY date(sale_date)""")

        data = cursor.fetchall()
        conn.close()

        if len(data) < 7:
            return "Insufficient data for trend analysis (need at least 7 days)"

        # Calculate trends
        metric_index = {"transactions": 1, "revenue": 2, "avg_transaction": 3}.get(
            metric, 2
        )
        values = [row[metric_index] for row in data]

        # Simple trend calculation
        first_week_avg = sum(values[:7]) / 7
        last_week_avg = sum(values[-7:]) / 7
        overall_avg = sum(values) / len(values)

        trend_direction = (
            "INCREASING" if last_week_avg > first_week_avg else "DECREASING"
        )
        change_percent = ((last_week_avg - first_week_avg) / first_week_avg) * 100

        # Detect anomalies
        anomalies = []
        for i, val in enumerate(values):
            if val > overall_avg * 1.5:
                anomalies.append(
                    f"Spike on {data[i][0]}: {val:.2f} (avg: {overall_avg:.2f})"
                )
            elif val < overall_avg * 0.5:
                anomalies.append(
                    f"Drop on {data[i][0]}: {val:.2f} (avg: {overall_avg:.2f})"
                )

        report = f"Trend Analysis - {metric.upper()}:\n\n"
        report += f"TREND DIRECTION: {trend_direction}\n"
        report += f"  First Week Average: ${first_week_avg:,.2f}\n"
        report += f"  Last Week Average: ${last_week_avg:,.2f}\n"
        report += f"  Change: {change_percent:+.1f}%\n\n"

        report += "OVERALL METRICS:\n"
        report += f"  Period Average: ${overall_avg:,.2f}\n"
        report += f"  Highest: ${max(values):,.2f}\n"
        report += f"  Lowest: ${min(values):,.2f}\n\n"

        if anomalies:
            report += "ANOMALIES DETECTED:\n"
            for anomaly in anomalies[:5]:
                report += f"  • {anomaly}\n"
        else:
            report += "NO SIGNIFICANT ANOMALIES DETECTED\n"

        return report

    except Exception as e:
        return f"Error detecting trends: {str(e)}"


@tool_registry.register_tool("calculate_kpis")
def calculate_kpis() -> str:
    """Calculate key performance indicators."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/analytics.db")

        if not os.path.exists(db_path):
            return "No analytics database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Revenue KPIs
        cursor.execute("""SELECT 
                            SUM(amount) as total_revenue,
                            AVG(amount) as avg_order_value,
                            COUNT(*) as total_orders
                          FROM sales
                          WHERE sale_date >= date('now', '-30 days')""")
        current_period = cursor.fetchone()

        cursor.execute("""SELECT 
                            SUM(amount) as total_revenue,
                            AVG(amount) as avg_order_value,
                            COUNT(*) as total_orders
                          FROM sales
                          WHERE sale_date >= date('now', '-60 days') 
                          AND sale_date < date('now', '-30 days')""")
        previous_period = cursor.fetchone()

        # Customer KPIs
        cursor.execute("""SELECT 
                            COUNT(DISTINCT customer_id) as unique_customers
                          FROM sales
                          WHERE sale_date >= date('now', '-30 days')""")
        current_customers = cursor.fetchone()[0]

        cursor.execute("""SELECT 
                            COUNT(DISTINCT customer_id) as unique_customers
                          FROM sales
                          WHERE sale_date >= date('now', '-60 days') 
                          AND sale_date < date('now', '-30 days')""")
        previous_customers = cursor.fetchone()[0]

        conn.close()

        # Calculate changes
        revenue_change = (
            ((current_period[0] - previous_period[0]) / previous_period[0] * 100)
            if previous_period[0]
            else 0
        )
        aov_change = (
            ((current_period[1] - previous_period[1]) / previous_period[1] * 100)
            if previous_period[1]
            else 0
        )
        customer_change = (
            ((current_customers - previous_customers) / previous_customers * 100)
            if previous_customers
            else 0
        )

        # Customer Lifetime Value (simplified)
        clv = current_period[0] / current_customers if current_customers else 0

        report = "Key Performance Indicators (KPIs):\n\n"
        report += "REVENUE METRICS (Last 30 Days):\n"
        report += f"  Total Revenue: ${current_period[0]:,.2f} ({revenue_change:+.1f}% vs previous period)\n"
        report += (
            f"  Average Order Value: ${current_period[1]:.2f} ({aov_change:+.1f}%)\n"
        )
        report += f"  Total Orders: {current_period[2]}\n\n"

        report += "CUSTOMER METRICS:\n"
        report += f"  Active Customers: {current_customers} ({customer_change:+.1f}%)\n"
        report += f"  Customer Lifetime Value: ${clv:.2f}\n"
        report += (
            f"  Orders per Customer: {current_period[2] / current_customers:.2f}\n\n"
        )

        report += "PERFORMANCE INDICATORS:\n"
        if revenue_change > 10:
            report += "  ✓ Strong revenue growth\n"
        elif revenue_change < -10:
            report += "  ⚠ Revenue decline - needs attention\n"
        else:
            report += "  • Stable revenue\n"

        if customer_change > 5:
            report += "  ✓ Growing customer base\n"
        elif customer_change < -5:
            report += "  ⚠ Declining customers - retention issue\n"
        else:
            report += "  • Stable customer base\n"

        return report

    except Exception as e:
        return f"Error calculating KPIs: {str(e)}"


@tool_registry.register_tool("generate_analytics_report")
def generate_analytics_report() -> str:
    """Generate comprehensive analytics report."""

    report = f"""
╔════════════════════════════════════════════════════════════════╗
║              BUSINESS ANALYTICS REPORT                         ║
║              Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}                    ║
╚════════════════════════════════════════════════════════════════╝

{calculate_kpis()}

{query_sales_data(30)}

{analyze_customer_behavior()}

{analyze_product_performance()}

{detect_trends("revenue")}

═══════════════════════════════════════════════════════════════
                    END OF REPORT
═══════════════════════════════════════════════════════════════
"""
    return report


@tool_registry.register_tool("save_report_to_file")
def save_report_to_file(
    report_content: str, report_name: str = "analytics_report"
) -> str:
    """Save analytics report to file."""

    try:
        reports_dir = os.path.expanduser("~/.OmniCoreAgent/reports")
        os.makedirs(reports_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{report_name}_{timestamp}.txt"
        filepath = os.path.join(reports_dir, filename)

        with open(filepath, "w") as f:
            f.write(report_content)

        return f"Report saved to: {filepath}"

    except Exception as e:
        return f"Error saving report: {str(e)}"


@tool_registry.register_tool("seed_analytics_data")
def seed_analytics_data() -> str:
    """Seed the database with sample analytics data."""
    import sqlite3
    import random
    from datetime import datetime

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/analytics.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create tables
        cursor.execute("""CREATE TABLE IF NOT EXISTS sales
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          customer_id INTEGER NOT NULL,
                          product_id INTEGER NOT NULL,
                          amount REAL NOT NULL,
                          sale_date DATETIME NOT NULL)""")

        # Clear existing data
        cursor.execute("DELETE FROM sales")

        # Generate 60 days of sales data
        products = range(1, 11)  # 10 products
        customers = range(1, 51)  # 50 customers

        for days_ago in range(60, 0, -1):
            sale_date = datetime.now() - timedelta(days=days_ago)

            # Generate 5-20 sales per day
            num_sales = random.randint(5, 20)

            for _ in range(num_sales):
                customer_id = random.choice(customers)
                product_id = random.choice(products)

                # Product pricing varies
                base_price = {
                    1: 29.99,
                    2: 49.99,
                    3: 99.99,
                    4: 149.99,
                    5: 19.99,
                    6: 79.99,
                    7: 199.99,
                    8: 39.99,
                    9: 129.99,
                    10: 59.99,
                }

                amount = base_price[product_id] * random.uniform(0.9, 1.1)

                cursor.execute(
                    """INSERT INTO sales (customer_id, product_id, amount, sale_date)
                                 VALUES (?, ?, ?, ?)""",
                    (customer_id, product_id, amount, sale_date),
                )

        conn.commit()

        # Get stats
        cursor.execute("SELECT COUNT(*), SUM(amount) FROM sales")
        count, total = cursor.fetchone()

        conn.close()

        return f"Database seeded successfully:\n  {count} sales transactions\n  ${total:,.2f} total revenue\n  60 days of data"

    except Exception as e:
        return f"Error seeding database: {str(e)}"


# ============================================================================
# DATA ANALYTICS AGENT CONFIGURATION
# ============================================================================

DATA_ANALYTICS_AGENT = {
    "agent_id": "data_analytics_agent",
    "system_instruction": """You are an AI Data Analytics Agent responsible for business intelligence and insights.

Your responsibilities:
1. Analyze sales data and identify trends
2. Calculate key performance indicators (KPIs)
3. Segment customers and analyze behavior
4. Evaluate product performance
5. Generate comprehensive business reports
6. Provide actionable insights and recommendations

Analysis Framework:

SALES ANALYSIS:
- Daily/weekly/monthly revenue trends
- Transaction volume and frequency
- Average order value
- Peak sales periods

CUSTOMER ANALYSIS:
- Customer segmentation (high/medium/low value)
- Purchase frequency and recency
- Customer lifetime value
- Retention and churn indicators

PRODUCT ANALYSIS:
- Best/worst performing products
- Revenue contribution by product
- Units sold vs revenue generated
- Product mix optimization

TREND DETECTION:
- Growth or decline patterns
- Seasonality indicators
- Anomaly detection (spikes or drops)
- Predictive indicators

REPORTING GUIDELINES:
- Present data clearly with specific numbers
- Include period-over-period comparisons
- Highlight key insights and anomalies
- Provide actionable recommendations
- Use percentage changes to show trends
- Identify opportunities and risks

When analyzing data:
1. Start with high-level KPIs
2. Drill down into specific metrics
3. Compare current vs previous periods
4. Identify outliers and trends
5. Synthesize insights into recommendations

Always base recommendations on data patterns, not assumptions.""",
    "model_config": {
        "provider": "openai",
        "model": "gpt-4.1",
        "temperature": 0.3,
        "max_context_length": 12000,
    },
    "mcp_tools": [],
    "local_tools": tool_registry,
    "agent_config": {
        "max_steps": 15,
        "tool_call_timeout": 60,
        "request_limit": 0,
        "memory_config": {"mode": "sliding_window", "value": 100},
    },
    "interval": 300,  # Every hour
    "max_retries": 2,
    "debug": True,
    "task_config": {
        "query": """Generate comprehensive business analytics:

1. Calculate current KPIs and compare to previous period
2. Query sales data for the last 30 days
3. Analyze customer behavior and segmentation
4. Evaluate product performance
5. Detect trends in revenue and other key metrics
6. Generate a complete analytics report with insights
7. Save the report to file

Provide specific insights about:
- What's working well (continue doing)
- What needs attention (concerns)
- Opportunities for growth
- Recommended actions

Be data-driven and specific in your analysis.""",
        "description": "Business intelligence and analytics reporting",
    },
}


# ============================================================================
# DEPLOYMENT AND TESTING
# ============================================================================


async def setup_and_run_agent():
    """Setup and run the data analytics agent."""

    print("=" * 80)
    print("DATA ANALYTICS AGENT - PRODUCTION DEPLOYMENT")
    print("=" * 80)
    print()

    # Step 1: Seed database with sample data
    print("Step 1: Seeding analytics database with sample data...")
    result = seed_analytics_data()
    print(result)
    print()

    # Step 2: Initialize agent components
    print("Step 2: Initializing agent components...")
    memory_router = MemoryRouter("database")
    event_router = EventRouter("in_memory")
    background_manager = BackgroundAgentManager(
        memory_router=memory_router, event_router=event_router
    )
    print("✓ Components initialized")
    print()

    # Step 3: Create the agent
    print("Step 3: Creating Data Analytics Agent...")
    result = await background_manager.create_agent(DATA_ANALYTICS_AGENT)
    print(f"✓ Agent created: {result['agent_id']}")
    print(f"  Session ID: {result['session_id']}")
    print()

    # Step 4: Start the background manager
    print("Step 4: Starting background agent manager...")
    background_manager.start()
    print("✓ Agent is now running automatically every hour")
    print()

    # Step 5: Show sample insights
    print("Step 5: Sample analytics preview:")
    print(calculate_kpis())
    print()

    print("=" * 80)
    print("AGENT IS NOW ACTIVE")
    print("=" * 80)
    print()
    print("The agent will:")
    print("  • Generate comprehensive analytics reports every hour")
    print("  • Calculate and track KPIs automatically")
    print("  • Detect trends and anomalies in business data")
    print("  • Provide actionable insights and recommendations")
    print("  • Save reports to ~/.OmniCoreAgent/reports/")
    print()
    print("Database: ~/.OmniCoreAgent/analytics.db")
    print("Reports: ~/.OmniCoreAgent/reports/")
    print()
    print("Analytics capabilities:")
    print("  • Sales performance tracking")
    print("  • Customer behavior analysis")
    print("  • Product performance evaluation")
    print("  • Trend detection and forecasting")
    print("  • KPI monitoring and alerts")
    print()
    print("Press Ctrl+C to stop the agent...")
    print()

    try:
        while True:
            await asyncio.sleep(60)

            status = background_manager.get_agent_status("data_analytics_agent")
            if status:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Agent Status: "
                    f"Runs: {status['run_count']}, Errors: {status['error_count']}"
                )

    except KeyboardInterrupt:
        print("\n\nShutting down agent...")
        background_manager.shutdown()
        print("✓ Agent stopped successfully")


def test_analytics_tools():
    """Test all analytics tools manually."""

    print("Testing Data Analytics Tools")
    print("=" * 80)
    print()

    print("Test 1: Seeding database...")
    print(seed_analytics_data())
    print()

    print("Test 2: Querying sales data...")
    print(query_sales_data(30))
    print()

    print("Test 3: Analyzing customer behavior...")
    print(analyze_customer_behavior())
    print()

    print("Test 4: Analyzing product performance...")
    print(analyze_product_performance())
    print()

    print("Test 5: Detecting trends...")
    print(detect_trends("revenue"))
    print()

    print("Test 6: Calculating KPIs...")
    print(calculate_kpis())
    print()

    print("Test 7: Generating full report...")
    report = generate_analytics_report()
    print(report)
    print()

    print("Test 8: Saving report to file...")
    print(save_report_to_file(report, "test_report"))
    print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_analytics_tools()
    else:
        asyncio.run(setup_and_run_agent())
