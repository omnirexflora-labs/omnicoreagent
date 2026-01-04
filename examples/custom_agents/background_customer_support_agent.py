#!/usr/bin/env python3
"""
AGENT #1: CUSTOMER SUPPORT AGENT
Real-world production agent for handling customer support tickets
"""

import os
import asyncio
from datetime import datetime
from omnicoreagent import (
    MemoryRouter,
    EventRouter,
    BackgroundAgentManager,
    ToolRegistry,
)

# Initialize tool registry
tool_registry = ToolRegistry()

# ============================================================================
# REAL-WORLD TOOLS FOR CUSTOMER SUPPORT
# ============================================================================


@tool_registry.register_tool("create_support_ticket")
def create_support_ticket(
    customer_email: str, subject: str, message: str, priority: str = "medium"
) -> str:
    """Create a new support ticket in the system."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.omni_core_agent/support.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create tickets table if not exists
        cursor.execute("""CREATE TABLE IF NOT EXISTS tickets
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          customer_email TEXT NOT NULL,
                          subject TEXT NOT NULL,
                          message TEXT NOT NULL,
                          priority TEXT NOT NULL,
                          status TEXT DEFAULT 'open',
                          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                          agent_notes TEXT)""")

        cursor.execute(
            """INSERT INTO tickets (customer_email, subject, message, priority)
                         VALUES (?, ?, ?, ?)""",
            (customer_email, subject, message, priority),
        )

        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return (
            f"Ticket #{ticket_id} created for {customer_email} - Priority: {priority}"
        )

    except Exception as e:
        return f"Error creating ticket: {str(e)}"


@tool_registry.register_tool("get_open_tickets")
def get_open_tickets(limit: int = 10) -> str:
    """Retrieve open support tickets."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.omni_core_agent/support.db")

        if not os.path.exists(db_path):
            return "No support database found. No open tickets."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """SELECT id, customer_email, subject, message, priority, created_at
                         FROM tickets WHERE status = 'open'
                         ORDER BY 
                           CASE priority 
                             WHEN 'critical' THEN 1
                             WHEN 'high' THEN 2
                             WHEN 'medium' THEN 3
                             WHEN 'low' THEN 4
                           END,
                           created_at ASC
                         LIMIT ?""",
            (limit,),
        )

        tickets = cursor.fetchall()
        conn.close()

        if not tickets:
            return "No open tickets at this time."

        result = f"Found {len(tickets)} open tickets:\n\n"
        for ticket in tickets:
            result += f"Ticket #{ticket[0]} [{ticket[4].upper()}]\n"
            result += f"  From: {ticket[1]}\n"
            result += f"  Subject: {ticket[2]}\n"
            result += f"  Message: {ticket[3][:150]}...\n"
            result += f"  Created: {ticket[5]}\n\n"

        return result

    except Exception as e:
        return f"Error retrieving tickets: {str(e)}"


@tool_registry.register_tool("respond_to_ticket")
def respond_to_ticket(
    ticket_id: int, response: str, new_status: str = "resolved"
) -> str:
    """Respond to a support ticket and update its status."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/support.db")

        if not os.path.exists(db_path):
            return "Support database not found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get ticket details
        cursor.execute(
            "SELECT customer_email, subject FROM tickets WHERE id = ?", (ticket_id,)
        )
        ticket = cursor.fetchone()

        if not ticket:
            conn.close()
            return f"Ticket #{ticket_id} not found."

        # Update ticket
        cursor.execute(
            """UPDATE tickets 
                         SET status = ?, agent_notes = ?, updated_at = CURRENT_TIMESTAMP
                         WHERE id = ?""",
            (new_status, response, ticket_id),
        )

        # Log response
        cursor.execute("""CREATE TABLE IF NOT EXISTS ticket_responses
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          ticket_id INTEGER,
                          response TEXT,
                          created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        cursor.execute(
            "INSERT INTO ticket_responses (ticket_id, response) VALUES (?, ?)",
            (ticket_id, response),
        )

        conn.commit()
        conn.close()

        return f"Response sent for Ticket #{ticket_id} to {ticket[0]}. Status: {new_status}"

    except Exception as e:
        return f"Error responding to ticket: {str(e)}"


@tool_registry.register_tool("categorize_ticket")
def categorize_ticket(ticket_id: int, category: str) -> str:
    """Categorize a support ticket (billing, technical, general, feature_request)."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.omni_core_agent/support.db")

        if not os.path.exists(db_path):
            return "Support database not found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Add category column if not exists
        try:
            cursor.execute("ALTER TABLE tickets ADD COLUMN category TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists

        cursor.execute(
            "UPDATE tickets SET category = ? WHERE id = ?", (category, ticket_id)
        )
        conn.commit()
        conn.close()

        return f"Ticket #{ticket_id} categorized as: {category}"

    except Exception as e:
        return f"Error categorizing ticket: {str(e)}"


@tool_registry.register_tool("escalate_ticket")
def escalate_ticket(ticket_id: int, reason: str) -> str:
    """Escalate a ticket to human support team."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.omni_core_agent/support.db")

        if not os.path.exists(db_path):
            return "Support database not found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Add escalated column if not exists
        try:
            cursor.execute("ALTER TABLE tickets ADD COLUMN escalated INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE tickets ADD COLUMN escalation_reason TEXT")
        except sqlite3.OperationalError:
            pass

        cursor.execute(
            """UPDATE tickets 
                         SET escalated = 1, escalation_reason = ?, updated_at = CURRENT_TIMESTAMP
                         WHERE id = ?""",
            (reason, ticket_id),
        )

        conn.commit()
        conn.close()

        return f"Ticket #{ticket_id} escalated to human support. Reason: {reason}"

    except Exception as e:
        return f"Error escalating ticket: {str(e)}"


@tool_registry.register_tool("get_ticket_stats")
def get_ticket_stats() -> str:
    """Get support ticket statistics."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.omni_core_agent/support.db")

        if not os.path.exists(db_path):
            return "No support database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Total tickets
        cursor.execute("SELECT COUNT(*) FROM tickets")
        total = cursor.fetchone()[0]

        # Open tickets
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "open"')
        open_count = cursor.fetchone()[0]

        # Resolved tickets
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "resolved"')
        resolved = cursor.fetchone()[0]

        # Escalated tickets
        cursor.execute("SELECT COUNT(*) FROM tickets WHERE escalated = 1")
        escalated = cursor.fetchone()[0]

        # By priority
        cursor.execute(
            'SELECT priority, COUNT(*) FROM tickets WHERE status = "open" GROUP BY priority'
        )
        priority_counts = cursor.fetchall()

        conn.close()

        stats = f"""Support Ticket Statistics:
  Total Tickets: {total}
  Open Tickets: {open_count}
  Resolved Tickets: {resolved}
  Escalated Tickets: {escalated}
  
  Open Tickets by Priority:"""

        for priority, count in priority_counts:
            stats += f"\n    {priority}: {count}"

        return stats

    except Exception as e:
        return f"Error getting stats: {str(e)}"


@tool_registry.register_tool("send_automated_response")
def send_automated_response(
    customer_email: str, ticket_id: int, response_type: str
) -> str:
    """Send automated response templates (acknowledgment, resolution, followup)."""

    templates = {
        "acknowledgment": f"""Dear Customer,

Thank you for contacting support. We have received your ticket #{ticket_id} and our team is reviewing your request.

We aim to respond within 24 hours for standard inquiries.

Best regards,
Support Team""",
        "resolution": f"""Dear Customer,

Your ticket #{ticket_id} has been resolved. If you continue to experience issues, please reply to this email.

Thank you for your patience.

Best regards,
Support Team""",
        "followup": f"""Dear Customer,

We wanted to follow up on ticket #{ticket_id}. Is everything working as expected?

Please let us know if you need further assistance.

Best regards,
Support Team""",
    }

    if response_type not in templates:
        return "Invalid response type. Use: acknowledgment, resolution, or followup"

    # In production, this would actually send an email
    # For now, we log it
    message = templates[response_type]

    try:
        import sqlite3

        db_path = os.path.expanduser("~/.omni_core_agent/support.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""CREATE TABLE IF NOT EXISTS sent_emails
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          ticket_id INTEGER,
                          customer_email TEXT,
                          response_type TEXT,
                          message TEXT,
                          sent_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        cursor.execute(
            """INSERT INTO sent_emails (ticket_id, customer_email, response_type, message)
                         VALUES (?, ?, ?, ?)""",
            (ticket_id, customer_email, response_type, message),
        )

        conn.commit()
        conn.close()

        return f"Automated {response_type} email sent to {customer_email} for ticket #{ticket_id}"

    except Exception as e:
        return f"Error sending automated response: {str(e)}"


@tool_registry.register_tool("search_knowledge_base")
def search_knowledge_base(query: str) -> str:
    """Search knowledge base for common solutions."""

    # Knowledge base of common issues and solutions
    knowledge_base = {
        "password reset": "To reset your password: 1) Click 'Forgot Password' on login page, 2) Enter your email, 3) Check your email for reset link, 4) Create new password",
        "login issues": "Common login issues: 1) Check caps lock is off, 2) Clear browser cache, 3) Try different browser, 4) Reset password if needed",
        "payment failed": "If payment fails: 1) Check card details are correct, 2) Ensure sufficient funds, 3) Try different payment method, 4) Contact your bank",
        "account activation": "To activate account: 1) Check email for activation link, 2) Click link within 24 hours, 3) Complete profile setup, 4) Contact support if link expired",
        "feature request": "To request features: 1) Check our roadmap, 2) Submit via feature request form, 3) Vote on existing requests, 4) Join our community forum",
        "billing inquiry": "For billing questions: 1) Check your invoice in account settings, 2) Review payment history, 3) Contact billing@company.com for specific questions",
        "technical error": "For technical errors: 1) Note error message/code, 2) Try clearing cache, 3) Check system status page, 4) Provide error details to support",
        "data export": "To export your data: 1) Go to Settings > Data, 2) Click 'Export Data', 3) Select date range, 4) Download when ready (can take up to 24hrs)",
    }

    query_lower = query.lower()
    matches = []

    for topic, solution in knowledge_base.items():
        if topic in query_lower or any(word in query_lower for word in topic.split()):
            matches.append(f"**{topic.title()}**\n{solution}")

    if matches:
        return "Knowledge Base Matches:\n\n" + "\n\n".join(matches)
    else:
        return "No direct matches found in knowledge base. This may require manual support."


# ============================================================================
# CUSTOMER SUPPORT AGENT CONFIGURATION
# ============================================================================

CUSTOMER_SUPPORT_AGENT = {
    "agent_id": "customer_support_agent",
    "system_instruction": """You are an AI Customer Support Agent for a SaaS company.

Your primary responsibilities:
1. Monitor incoming support tickets every 5 minutes
2. Read and understand customer issues
3. Search knowledge base for solutions
4. Provide helpful, professional responses
5. Categorize tickets (billing, technical, general, feature_request)
6. Escalate complex issues to human agents
7. Track resolution metrics

Response Guidelines:
- Be empathetic and professional
- Provide clear, actionable solutions
- Use knowledge base when available
- Acknowledge customer frustration
- Set clear expectations for response times
- Escalate when: customer is angry, issue is complex, refund requested, legal matter, account security issue

Always use send_automated_response for acknowledgment first, then provide detailed solution.

Categories:
- billing: payments, invoices, subscriptions
- technical: bugs, errors, performance issues
- general: questions, how-to, information
- feature_request: new feature requests, improvements

Escalate if you cannot resolve or if customer explicitly requests human support.""",
    "model_config": {
        "provider": "openai",
        "model": "gpt-4.1",
        "temperature": 0.4,  # Warm enough to be human-like, consistent enough for support
        "max_context_length": 8000,
    },
    "mcp_tools": [],  # Using local tools only
    "local_tools": tool_registry,
    "agent_config": {
        "max_steps": 15,  # May need multiple steps per ticket
        "tool_call_timeout": 45,
        "request_limit": 0,  # Unlimited
        "memory_config": {"mode": "sliding_window", "value": 100},
    },
    "interval": 300,  # Check every 5 minutes
    "max_retries": 3,
    "debug": True,
    "task_config": {
        "query": """Process customer support tickets:

1. Get all open tickets using get_open_tickets
2. For EACH open ticket:
   a. Read the customer's issue carefully
   b. Search knowledge base for relevant solutions
   c. Categorize the ticket appropriately
   d. If you can resolve it:
      - Send acknowledgment email
      - Provide detailed solution in response
      - Mark ticket as resolved
   e. If you cannot resolve it:
      - Send acknowledgment email
      - Escalate to human support with clear reason
3. Generate summary statistics using get_ticket_stats

Be thorough and helpful. Customer satisfaction is priority.""",
        "description": "Automated customer support ticket processing",
    },
}


# ============================================================================
# DEPLOYMENT AND TESTING
# ============================================================================


async def setup_and_run_agent():
    """Setup and run the customer support agent."""

    print("=" * 80)
    print("CUSTOMER SUPPORT AGENT - PRODUCTION DEPLOYMENT")
    print("=" * 80)
    print()

    # Step 1: Create sample tickets for testing
    print("Step 1: Creating sample support tickets...")
    create_support_ticket(
        "john@example.com",
        "Cannot login to my account",
        "I've been trying to login for the past hour but keep getting 'invalid credentials' error. I'm sure my password is correct.",
        "high",
    )
    create_support_ticket(
        "sarah@company.com",
        "Question about billing",
        "I was charged twice this month. Can you please check my invoice?",
        "medium",
    )
    create_support_ticket(
        "mike@startup.io",
        "Feature request",
        "Would love to see dark mode added to the dashboard!",
        "low",
    )
    create_support_ticket(
        "admin@bigcorp.com",
        "Critical: Data export not working",
        "Urgent! We need to export our data for compliance audit but the export button is not responding.",
        "critical",
    )
    print("✓ Created 4 sample tickets")
    print()

    # Step 2: Initialize agent components
    print("Step 2: Initializing agent components...")
    memory_router = MemoryRouter("in_memory")
    event_router = EventRouter("in_memory")
    background_manager = BackgroundAgentManager(
        memory_router=memory_router, event_router=event_router
    )
    print("✓ Components initialized")
    print()

    # Step 3: Create the agent
    print("Step 3: Creating Customer Support Agent...")
    result = await background_manager.create_agent(CUSTOMER_SUPPORT_AGENT)
    print(f"✓ Agent created: {result['agent_id']}")
    print(f"  Session ID: {result['session_id']}")
    print()

    # Step 4: Start the background manager
    print("Step 4: Starting background agent manager...")
    background_manager.start()
    print("✓ Agent is now running automatically every 5 minutes")
    print()

    # Step 5: Show current status
    print("Step 5: Current ticket status:")
    print(get_open_tickets())
    print()

    print("=" * 80)
    print("AGENT IS NOW ACTIVE")
    print("=" * 80)
    print()
    print("The agent will:")
    print("  • Check for new tickets every 5 minutes")
    print("  • Automatically respond to tickets it can handle")
    print("  • Escalate complex issues to human support")
    print("  • Track all interactions in the database")
    print()
    print("Database location: ~/.OmniCoreAgent/support.db")
    print()
    print("To monitor the agent:")
    print("  • Check logs in real-time")
    print("  • View ticket stats with get_ticket_stats()")
    print("  • Monitor agent status with background_manager.get_agent_status()")
    print()
    print("Press Ctrl+C to stop the agent...")
    print()

    try:
        # Keep running
        while True:
            await asyncio.sleep(60)

            # Show status update every minute
            status = background_manager.get_agent_status("customer_support_agent")
            if status:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Agent Status: "
                    f"Runs: {status['run_count']}, Errors: {status['error_count']}"
                )

    except KeyboardInterrupt:
        print("\n\nShutting down agent...")
        background_manager.shutdown()
        print("✓ Agent stopped successfully")


# ============================================================================
# MANUAL TESTING FUNCTIONS
# ============================================================================


def test_customer_support_tools():
    """Test all customer support tools manually."""

    print("Testing Customer Support Tools")
    print("=" * 80)
    print()

    # Test 1: Create tickets
    print("Test 1: Creating support tickets...")
    print(
        create_support_ticket(
            "test1@example.com", "Test ticket 1", "This is a test message", "high"
        )
    )
    print(
        create_support_ticket(
            "test2@example.com", "Password reset help", "I forgot my password", "medium"
        )
    )
    print()

    # Test 2: Get open tickets
    print("Test 2: Retrieving open tickets...")
    print(get_open_tickets())
    print()

    # Test 3: Search knowledge base
    print("Test 3: Searching knowledge base...")
    print(search_knowledge_base("password reset"))
    print()

    # Test 4: Categorize ticket
    print("Test 4: Categorizing ticket...")
    print(categorize_ticket(1, "technical"))
    print()

    # Test 5: Send automated response
    print("Test 5: Sending automated response...")
    print(send_automated_response("test1@example.com", 1, "acknowledgment"))
    print()

    # Test 6: Respond to ticket
    print("Test 6: Responding to ticket...")
    print(
        respond_to_ticket(
            2, "Password reset link has been sent to your email.", "resolved"
        )
    )
    print()

    # Test 7: Escalate ticket
    print("Test 7: Escalating ticket...")
    print(escalate_ticket(1, "Customer is requesting refund - requires human approval"))
    print()

    # Test 8: Get statistics
    print("Test 8: Getting ticket statistics...")
    print(get_ticket_stats())
    print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Run manual tests
        test_customer_support_tools()
    else:
        # Run the full agent
        asyncio.run(setup_and_run_agent())
