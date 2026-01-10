# Advanced Agent Examples

> Real-world applications that demonstrate the full power of OmniCoreAgent.

These are not simple demos — they're **production-ready patterns** you can adapt for your own projects.

---

## 🎯 Examples

| Application | Description | Lines of Code |
|-------------|-------------|---------------|
| [E-commerce Personal Shopper](./e_commerce_personal_shopper_agent.py) | Shopping assistant with cart, preferences, and recommendations | ~700 |
| [Flight Booking Agent](./flightBooking_agent.py) | Travel agent with search, booking, and itinerary management | ~300 |
| [Customer Support Agent](./real_time_customer_support_agent.py) | Support agent with ticket handling and escalation | ~600 |
| [AI Due Diligence](./ai_due_diligence_agent/) | Investment research with web search, analysis, and reporting | ~1000+ |

---

## 🛍️ E-commerce Personal Shopper

A full-featured shopping assistant with:
- Product search across catalogs
- Shopping cart management  
- User preference learning
- Personalized recommendations
- Price comparison
- Shipping calculations

**Key Patterns**:
```python
@tools.register_tool("search_products")
def search_products(query: str, category: str = "all") -> str:
    """Search products with filters."""
    ...

@tools.register_tool("add_to_cart")  
def add_to_cart(session_id: str, product_id: str, quantity: int = 1) -> str:
    """Add items to shopping cart."""
    ...
```

---

## ✈️ Flight Booking Agent

A conversational travel agent with:
- Flight search
- Booking management
- Itinerary creation
- Price alerts

---

## 📞 Customer Support Agent

A support agent that handles:
- Ticket creation and tracking
- Knowledge base queries
- Escalation to human agents
- Sentiment analysis

---

## 📊 AI Due Diligence Agent

A complete investment research pipeline:
- Web research with search tools
- Company analysis
- Report generation
- Multi-agent workflow

See the [full documentation](./ai_due_diligence_agent/README.md) for details.

---

## 🔧 Common Patterns in These Examples

### 1. Tool Registration
```python
tools = ToolRegistry()

@tools.register_tool("tool_name")
def my_tool(param: str) -> dict:
    """Description for the LLM."""
    return {"status": "success", "data": ...}
```

### 2. Session Management
```python
async def handle_request(user_id: str, message: str):
    result = await agent.run(message, session_id=user_id)
    return result["response"]
```

### 3. Production Configuration
```python
agent = OmniCoreAgent(
    name="production_agent",
    agent_config={
        "max_steps": 15,
        "context_management": {"enabled": True},
        "guardrail_config": {"enabled": True},
        "memory_config": {
            "summary": {"enabled": True}
        },
    },
)
```

---

**Previous**: [Workflows](../workflows) — Multi-agent orchestration  
**Next**: [Showcase](../showcase) — Full production applications with Docker
