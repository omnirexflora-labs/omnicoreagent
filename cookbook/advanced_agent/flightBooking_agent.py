# examples/flightBooking_agent.py
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from omnicoreagent import OmniCoreAgent, MemoryRouter, ToolRegistry, logger

from _bootstrap import model_config, require_llm_api_key, response_text

# ------------------ Config / CRM bootstrap ------------------ #
CRM_FILE = str(Path("tmp/cookbook_flight_crm.json"))


def create_crm(path: str = CRM_FILE) -> None:
    """Create a deterministic sample CRM JSON file for the demo run."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    sample = {
        "users": [
            {
                "id": "U1001",
                "name": "John Doe",
                "email": "john@example.com",
                "loyalty_status": "Gold",
                "bookings": [
                    {
                        "booking_id": "B2001",
                        "flight": "NYC-LON-2025-09-01-F3001",
                        "status": "confirmed",
                    }
                ],
            },
            {
                "id": "U1002",
                "name": "Jane Smith",
                "email": "jane@example.com",
                "loyalty_status": "Silver",
                "bookings": [],
            },
        ]
    }
    with open(path, "w") as f:
        json.dump(sample, f, indent=2)


# ------------------ Tool Registry ------------------ #
local_tools = ToolRegistry()


@local_tools.register_tool(
    name="search_flights",
    description="Search available flights between origin and destination on a given date",
    inputSchema={
        "type": "object",
        "properties": {
            "origin": {
                "type": "string",
                "maxLength": 10,
                "description": "Origin airport or city code",
            },
            "destination": {
                "type": "string",
                "maxLength": 10,
                "description": "Destination airport or city code",
            },
            "date": {
                "type": "string",
                "format": "date",
                "description": "Travel date YYYY-MM-DD",
            },
        },
        "required": ["origin", "destination", "date"],
        "additionalProperties": False,
    },
)
def search_flights(origin: str, destination: str, date: str) -> dict:
    """Return a small set of mocked flights for given origin/destination/date."""
    flights = [
        {
            "flight_id": "F3001",
            "route": f"{origin}-{destination}",
            "date": date,
            "price": 550,
            "class": "Economy",
        },
        {
            "flight_id": "F3002",
            "route": f"{origin}-{destination}",
            "date": date,
            "price": 720,
            "class": "Economy Plus",
        },
        {
            "flight_id": "F3003",
            "route": f"{origin}-{destination}",
            "date": date,
            "price": 1200,
            "class": "Business",
        },
    ]
    return {"flights": flights}


@local_tools.register_tool(
    name="book_flight",
    description="Book a flight for a user (creates booking in CRM)",
    inputSchema={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "maxLength": 100},
            "flight_id": {"type": "string", "maxLength": 100},
        },
        "required": ["user_id", "flight_id"],
        "additionalProperties": False,
    },
)
def book_flight(user_id: str, flight_id: str) -> dict:
    """Create a booking entry in crm.json for the given user."""
    if not os.path.exists(CRM_FILE):
        return {"error": "CRM not found"}

    with open(CRM_FILE, "r") as f:
        data = json.load(f)

    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return {"error": f"User {user_id} not found"}

    booking_id = f"B{2000 + sum(len(u.get('bookings', [])) for u in data['users']) + 1}"
    booking = {"booking_id": booking_id, "flight": flight_id, "status": "confirmed"}
    user.setdefault("bookings", []).append(booking)

    with open(CRM_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return {
        "status": "success",
        "message": f"Booking confirmed: {booking_id} for user {user['name']}",
        "booking": booking,
    }


@local_tools.register_tool(
    name="cancel_booking",
    description="Cancel an existing booking for a user",
    inputSchema={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "maxLength": 100},
            "booking_id": {"type": "string", "maxLength": 100},
        },
        "required": ["user_id", "booking_id"],
        "additionalProperties": False,
    },
)
def cancel_booking(user_id: str, booking_id: str) -> dict:
    """Mark a booking canceled in crm.json."""
    if not os.path.exists(CRM_FILE):
        return {"error": "CRM not found"}

    with open(CRM_FILE, "r") as f:
        data = json.load(f)

    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return {"error": f"User {user_id} not found"}

    booking = next(
        (b for b in user.get("bookings", []) if b["booking_id"] == booking_id), None
    )
    if not booking:
        return {"error": f"Booking {booking_id} not found for user {user_id}"}

    booking["status"] = "canceled"

    with open(CRM_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return {
        "status": "success",
        "message": f"Booking {booking_id} canceled for user {user['name']}",
        "booking": booking,
    }


@local_tools.register_tool(
    name="get_customer_profile",
    description="Retrieve a customer's profile from CRM",
    inputSchema={
        "type": "object",
        "properties": {"user_id": {"type": "string", "maxLength": 100}},
        "required": ["user_id"],
        "additionalProperties": False,
    },
)
def get_customer_profile(user_id: str) -> dict:
    """Return the user object from crm.json."""
    if not os.path.exists(CRM_FILE):
        return {"error": "CRM not found"}

    with open(CRM_FILE, "r") as f:
        data = json.load(f)

    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return {"error": f"User {user_id} not found"}

    return {"user": user}


@local_tools.register_tool(
    name="think",
    description="Use the tool to think about something. It will not obtain new information or change the database, but just append the thought to the log. Use it when complex reasoning or some cache memory is needed.",
    inputSchema={
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "A thought to think about.",
            }
        },
        "required": ["thought"],
    },
)
def think(thought: str) -> dict:
    """Return structured reasoning steps for multi-step tasks."""
    return {"thought": thought}


logger.info("✈️ Flight Booking Tools registered")


# ------------------ CLI / Agent ------------------ #
class AirlineAgentCLI:
    """CLI interface for Airline Booking Agent. initialize() once and reuse the agent."""

    def __init__(self):
        self.agent: Optional[OmniCoreAgent] = None
        self.memory_router: Optional[MemoryRouter] = None

    async def initialize(self):
        """Initialize the airline agent (instantiate routers and the OmniCoreAgent)."""
        require_llm_api_key()
        create_crm()  # bootstrap crm.json if missing
        print("🚀 Initializing Flight Booking Agent...")

        self.memory_router = MemoryRouter("in_memory")

        self.agent = OmniCoreAgent(
            name="flight_booking_agent",
            system_instruction=(
                "You are an expert Flight Booking Agent with access to flight search, booking, cancellation, "
                "and customer profile tools. You MUST use the `think` tool as your reasoning scratchpad for "
                "ANY multi-step or complex requests before taking action.\n\n"
                "## CRITICAL: Always use the think tool first for complex requests\n\n"
                "**When to use think tool:**\n"
                "- Multi-step operations (search + book, cancel + rebook)\n"
                "- Complex requests requiring policy verification\n"
                "- Requests needing customer profile analysis\n"
                "- Operations requiring rule compliance checks\n"
                "- Any request with multiple conditions or constraints\n\n"
                "**Think tool workflow:**\n"
                "1. **Analyze the request** - Break down what's needed\n"
                "2. **List applicable rules** - Identify policies, restrictions, requirements\n"
                "3. **Check information gaps** - What data do you need vs. what you have\n"
                "4. **Verify compliance** - Ensure planned actions follow all policies\n"
                "5. **Plan execution steps** - Outline the exact sequence of tool calls\n"
                "6. **Validate assumptions** - Check if your plan makes sense\n\n"
                "**Examples of think tool usage:**\n"
                "- User: 'Book cheapest flight for John on Sept 1'\n"
                "  → Think: Need user ID, search flights, compare prices, verify payment methods, check baggage rules\n"
                "- User: 'Cancel John's booking and rebook tomorrow'\n"
                "  → Think: Verify cancellation policy, check rebooking rules, ensure no double-booking, calculate fees\n"
                "- User: 'Book 3 tickets with 2 bags each'\n"
                "  → Think: Check membership tier for baggage allowance, verify payment method limits, calculate total costs\n\n"
                "**After thinking, execute your plan step-by-step using the appropriate tools.**\n"
                "Always include booking IDs in confirmations and keep responses professional but concise.\n\n"
                "**Never ask the user to provide tool output. If you need customer data, flight data, booking data, or cancellation data, call the matching tool yourself.**\n"
                "- Flight search requests: call search_flights.\n"
                "- Booking requests: call get_customer_profile when needed, then search_flights if needed, then book_flight.\n"
                "- Profile requests: call get_customer_profile.\n"
                "- Cancellation requests: call cancel_booking.\n\n"
                "**Remember:** The think tool is your strategic planning center. Use it to avoid mistakes and ensure compliance with all airline policies and customer requirements."
            ),
            model_config=model_config(temperature=0.2, max_tokens=900),
            local_tools=local_tools,
            agent_config={
                "max_steps": 12,
                "tool_call_timeout": 45,
                "request_limit": 0,
                "memory_config": {"mode": "token_budget", "value": 8000},
            },
            memory_router=self.memory_router,
            debug=False,
        )

    async def demo_flow(self):
        """Show sample interactions."""
        session_id = "flight_booking_demo"
        queries = [
            "Search flights from NYC to LON on 2025-09-01",
            "Book the cheapest flight on 2025-09-01 for user U1001",
            "Get profile for user U1001",
        ]

        for q in queries:
            print(f"\nUser: {q}")
            result = await self.agent.run(q, session_id=session_id)
            print("Agent:", response_text(result))


# ------------------ Entrypoint ------------------ #
async def main():
    cli = AirlineAgentCLI()
    await cli.initialize()
    await cli.demo_flow()


if __name__ == "__main__":
    asyncio.run(main())
