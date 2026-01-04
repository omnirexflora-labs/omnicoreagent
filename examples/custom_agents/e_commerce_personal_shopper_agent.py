#!/usr/bin/env python3
"""
Production E-commerce Personal Shopper Agent
Real integrations with Shopify, WooCommerce, and inventory systems
"""

import os
import requests
from datetime import datetime
from typing import Dict, List
from omnicoreagent import OmniCoreAgent, ToolRegistry


class ProductionEcommerceAgent:
    def __init__(self):
        self.tool_registry = ToolRegistry()
        self.setup_ecommerce_tools()
        self.user_profiles = {}  # User preference database
        self.cart_sessions = {}  # Shopping cart sessions
        self.product_catalog = self.load_product_catalog()

    def load_product_catalog(self) -> Dict:
        """Load product catalog from real data source"""
        # In production, this would be from Shopify API, database, or CSV
        return {
            "electronics": [
                {
                    "id": "ELEC-001",
                    "name": "Wireless Headphones",
                    "price": 129.99,
                    "brand": "Sony",
                    "rating": 4.5,
                    "stock": 45,
                },
                {
                    "id": "ELEC-002",
                    "name": "Smart Watch",
                    "price": 299.99,
                    "brand": "Apple",
                    "rating": 4.8,
                    "stock": 23,
                },
                {
                    "id": "ELEC-003",
                    "name": "Tablet",
                    "price": 459.99,
                    "brand": "Samsung",
                    "rating": 4.3,
                    "stock": 15,
                },
                {
                    "id": "ELEC-004",
                    "name": "Gaming Console",
                    "price": 499.99,
                    "brand": "Sony",
                    "rating": 4.7,
                    "stock": 8,
                },
                {
                    "id": "ELEC-005",
                    "name": "Laptop",
                    "price": 1299.99,
                    "brand": "Dell",
                    "rating": 4.6,
                    "stock": 12,
                },
            ],
            "clothing": [
                {
                    "id": "CLTH-001",
                    "name": "Winter Jacket",
                    "price": 89.99,
                    "brand": "North Face",
                    "rating": 4.4,
                    "stock": 67,
                },
                {
                    "id": "CLTH-002",
                    "name": "Running Shoes",
                    "price": 129.99,
                    "brand": "Nike",
                    "rating": 4.7,
                    "stock": 89,
                },
                {
                    "id": "CLTH-003",
                    "name": "Jeans",
                    "price": 49.99,
                    "brand": "Levi's",
                    "rating": 4.2,
                    "stock": 156,
                },
                {
                    "id": "CLTH-004",
                    "name": "Dress Shirt",
                    "price": 39.99,
                    "brand": "Calvin Klein",
                    "rating": 4.3,
                    "stock": 78,
                },
            ],
            "home": [
                {
                    "id": "HOME-001",
                    "name": "Coffee Maker",
                    "price": 79.99,
                    "brand": "Keurig",
                    "rating": 4.1,
                    "stock": 34,
                },
                {
                    "id": "HOME-002",
                    "name": "Air Purifier",
                    "price": 199.99,
                    "brand": "Dyson",
                    "rating": 4.6,
                    "stock": 22,
                },
                {
                    "id": "HOME-003",
                    "name": "Blender",
                    "price": 49.99,
                    "brand": "Ninja",
                    "rating": 4.4,
                    "stock": 41,
                },
            ],
        }

    def setup_ecommerce_tools(self):
        """Setup production-ready e-commerce tools with real integrations"""

        # Shopify/WooCommerce Integration
        @self.tool_registry.register_tool("search_products")
        def search_products(
            query: str,
            category: str = "all",
            price_min: float = 0,
            price_max: float = 10000,
            brand: str = "",
            sort_by: str = "relevance",
        ) -> str:
            """Search products across e-commerce platforms with real API integration."""
            try:
                # Shopify API Integration
                shopify_url = os.getenv("SHOPIFY_STORE_URL")
                shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")

                if shopify_url and shopify_token:
                    headers = {"X-Shopify-Access-Token": shopify_token}
                    search_params = {
                        "title": query,
                        "vendor": brand if brand else None,
                        "product_type": category if category != "all" else None,
                    }

                    # Remove None values
                    search_params = {
                        k: v for k, v in search_params.items() if v is not None
                    }

                    response = requests.get(
                        f"{shopify_url}/admin/api/2024-01/products.json",
                        headers=headers,
                        params=search_params,
                    )

                    if response.status_code == 200:
                        products = response.json().get("products", [])
                        filtered_products = []

                        for product in products:
                            variants = product.get("variants", [{}])
                            price = (
                                float(variants[0].get("price", 0)) if variants else 0
                            )

                            if price_min <= price <= price_max:
                                filtered_products.append(
                                    {
                                        "id": product["id"],
                                        "title": product["title"],
                                        "vendor": product.get("vendor", ""),
                                        "price": price,
                                        "in_stock": variants[0].get(
                                            "inventory_quantity", 0
                                        )
                                        > 0
                                        if variants
                                        else False,
                                    }
                                )

                        if filtered_products:
                            results = []
                            for product in filtered_products[:8]:
                                stock_status = (
                                    "✅ In Stock"
                                    if product["in_stock"]
                                    else "❌ Out of Stock"
                                )
                                results.append(
                                    f"• {product['title']} - ${product['price']} ({product['vendor']}) - {stock_status}"
                                )

                            return (
                                f"🛍️ Found {len(filtered_products)} products:\n"
                                + "\n".join(results)
                            )

                # Fallback to internal catalog
                all_products = []
                for cat_products in self.product_catalog.values():
                    all_products.extend(cat_products)

                matching_products = []
                for product in all_products:
                    matches_query = (
                        query.lower() in product["name"].lower()
                        or query.lower() in product["brand"].lower()
                    )
                    matches_category = category == "all" or any(
                        category in cat
                        for cat in self.product_catalog.keys()
                        if product in self.product_catalog[cat]
                    )
                    matches_price = price_min <= product["price"] <= price_max
                    matches_brand = (
                        not brand or brand.lower() in product["brand"].lower()
                    )

                    if (
                        matches_query
                        and matches_category
                        and matches_price
                        and matches_brand
                    ):
                        matching_products.append(product)

                # Sort products
                if sort_by == "price_low":
                    matching_products.sort(key=lambda x: x["price"])
                elif sort_by == "price_high":
                    matching_products.sort(key=lambda x: x["price"], reverse=True)
                elif sort_by == "rating":
                    matching_products.sort(key=lambda x: x["rating"], reverse=True)

                if matching_products:
                    results = []
                    for product in matching_products[:8]:
                        stock_status = (
                            f"📦 {product['stock']} left"
                            if product["stock"] > 0
                            else "❌ Out of stock"
                        )
                        results.append(
                            f"• {product['name']} - ${product['price']} ({product['brand']}) ⭐ {product['rating']} - {stock_status}"
                        )

                    return f"🛍️ Found {len(matching_products)} products:\n" + "\n".join(
                        results
                    )
                else:
                    return f"❌ No products found for '{query}'. Try different search terms."

            except Exception as e:
                return f"❌ Product search error: {str(e)}"

        # Real Inventory Management
        @self.tool_registry.register_tool("check_inventory")
        def check_inventory(product_id: str, location: str = "warehouse") -> str:
            """Check real-time inventory levels across locations."""
            try:
                # Integration with inventory management system
                inventory_api = os.getenv("INVENTORY_API_URL")
                api_key = os.getenv("INVENTORY_API_KEY")

                if inventory_api and api_key:
                    headers = {"X-API-Key": api_key}
                    params = {"product_id": product_id, "location": location}

                    response = requests.get(
                        f"{inventory_api}/stock", headers=headers, params=params
                    )
                    if response.status_code == 200:
                        stock_data = response.json()
                        return f"""📊 Inventory for {product_id}
• Current Stock: {stock_data.get("quantity", 0)}
• Location: {stock_data.get("location", location)}
• Reorder Level: {stock_data.get("reorder_level", 10)}
• Expected Restock: {stock_data.get("restock_date", "N/A")}"""

                # Fallback to catalog check
                for category_products in self.product_catalog.values():
                    for product in category_products:
                        if product["id"] == product_id:
                            stock_status = (
                                "✅ In Stock"
                                if product["stock"] > 10
                                else "⚠️ Low Stock"
                                if product["stock"] > 0
                                else "❌ Out of Stock"
                            )
                            return f"""📊 Inventory for {product["name"]} ({product_id})
• Current Stock: {product["stock"]} units
• Status: {stock_status}
• Location: {location}
• Price: ${product["price"]}"""

                return f"❌ Product {product_id} not found in inventory system."

            except Exception as e:
                return f"❌ Inventory check error: {str(e)}"

        # Shopping Cart Management
        @self.tool_registry.register_tool("add_to_cart")
        def add_to_cart(session_id: str, product_id: str, quantity: int = 1) -> str:
            """Add items to shopping cart with session management."""
            try:
                if session_id not in self.cart_sessions:
                    self.cart_sessions[session_id] = {
                        "created_at": datetime.now(),
                        "items": [],
                        "total": 0.0,
                    }

                # Find product in catalog
                product_found = None
                for category_products in self.product_catalog.values():
                    for product in category_products:
                        if product["id"] == product_id:
                            product_found = product
                            break
                    if product_found:
                        break

                if not product_found:
                    return f"❌ Product {product_id} not found."

                if product_found["stock"] < quantity:
                    return f"❌ Only {product_found['stock']} units available for {product_found['name']}."

                # Add to cart
                cart_item = {
                    "product_id": product_id,
                    "name": product_found["name"],
                    "price": product_found["price"],
                    "quantity": quantity,
                    "subtotal": product_found["price"] * quantity,
                }

                self.cart_sessions[session_id]["items"].append(cart_item)
                self.cart_sessions[session_id]["total"] += cart_item["subtotal"]

                return f"✅ Added {quantity}x {product_found['name']} to cart. Subtotal: ${cart_item['subtotal']:.2f}"

            except Exception as e:
                return f"❌ Cart error: {str(e)}"

        @self.tool_registry.register_tool("view_cart")
        def view_cart(session_id: str) -> str:
            """View current shopping cart contents."""
            try:
                if (
                    session_id not in self.cart_sessions
                    or not self.cart_sessions[session_id]["items"]
                ):
                    return "🛒 Your cart is empty."

                cart = self.cart_sessions[session_id]
                items_text = []

                for i, item in enumerate(cart["items"], 1):
                    items_text.append(
                        f"{i}. {item['name']} - {item['quantity']}x @ ${item['price']} = ${item['subtotal']:.2f}"
                    )

                cart_summary = "\n".join(items_text)
                return f"""🛒 Shopping Cart (Session: {session_id})
{cart_summary}
💳 Total: ${cart["total"]:.2f}"""

            except Exception as e:
                return f"❌ Cart view error: {str(e)}"

        # User Preference Learning
        @self.tool_registry.register_tool("save_user_preferences")
        def save_user_preferences(
            user_id: str,
            preferred_categories: List[str],
            price_range: str = "0-500",
            favorite_brands: List[str] = None,
            style_preferences: str = "",
        ) -> str:
            """Save user shopping preferences for personalized recommendations."""
            try:
                if user_id not in self.user_profiles:
                    self.user_profiles[user_id] = {
                        "created_at": datetime.now(),
                        "preferences": {},
                        "browse_history": [],
                        "purchase_history": [],
                    }

                self.user_profiles[user_id]["preferences"] = {
                    "categories": preferred_categories,
                    "price_range": price_range,
                    "brands": favorite_brands or [],
                    "style": style_preferences,
                    "updated_at": datetime.now(),
                }

                return f"✅ Preferences saved for user {user_id}. We'll provide better recommendations!"

            except Exception as e:
                return f"❌ Preference save error: {str(e)}"

        # Personalized Recommendations
        @self.tool_registry.register_tool("get_recommendations")
        def get_recommendations(user_id: str, max_results: int = 6) -> str:
            """Get personalized product recommendations based on user preferences."""
            try:
                if user_id not in self.user_profiles:
                    return "❌ No preferences found. Please set your preferences first using save_user_preferences."

                preferences = self.user_profiles[user_id]["preferences"]
                preferred_categories = preferences.get("categories", [])
                price_range = preferences.get("price_range", "0-500")
                favorite_brands = preferences.get("brands", [])

                # Parse price range
                try:
                    price_min, price_max = map(float, price_range.split("-"))
                except:
                    price_min, price_max = 0, 500

                # Find matching products
                recommendations = []
                for category in preferred_categories:
                    if category in self.product_catalog:
                        for product in self.product_catalog[category]:
                            if price_min <= product["price"] <= price_max:
                                brand_match = not favorite_brands or any(
                                    brand.lower() in product["brand"].lower()
                                    for brand in favorite_brands
                                )
                                if brand_match and product["stock"] > 0:
                                    recommendations.append(product)

                # Sort by rating and take top results
                recommendations.sort(key=lambda x: x["rating"], reverse=True)
                top_recommendations = recommendations[:max_results]

                if top_recommendations:
                    results = []
                    for product in top_recommendations:
                        results.append(
                            f"• {product['name']} - ${product['price']} ({product['brand']}) ⭐ {product['rating']}"
                        )

                    return "🎯 Personalized Recommendations for you:\n" + "\n".join(
                        results
                    )
                else:
                    return "❌ No recommendations found. Try updating your preferences."

            except Exception as e:
                return f"❌ Recommendation error: {str(e)}"

        # Price Comparison
        @self.tool_registry.register_tool("compare_prices")
        def compare_prices(product_name: str, competitors: List[str] = None) -> str:
            """Compare prices across different retailers."""
            try:
                # This would integrate with price comparison APIs
                competitor_prices = {
                    "amazon": {"price": 125.99, "rating": 4.6, "shipping": "free"},
                    "walmart": {"price": 129.99, "rating": 4.4, "shipping": "$5.99"},
                    "bestbuy": {"price": 134.99, "rating": 4.5, "shipping": "free"},
                    "target": {
                        "price": 131.99,
                        "rating": 4.3,
                        "shipping": "free over $35",
                    },
                }

                if competitors:
                    competitor_prices = {
                        k: v for k, v in competitor_prices.items() if k in competitors
                    }

                comparison_text = []
                for competitor, data in competitor_prices.items():
                    comparison_text.append(
                        f"• {competitor.title()}: ${data['price']} ⭐ {data['rating']} (Shipping: {data['shipping']})"
                    )

                return f"💰 Price Comparison for {product_name}:\n" + "\n".join(
                    comparison_text
                )

            except Exception as e:
                return f"❌ Price comparison error: {str(e)}"

        # Shipping and Delivery
        @self.tool_registry.register_tool("check_shipping")
        def check_shipping(zip_code: str, cart_total: float = 0) -> str:
            """Check shipping options and delivery times."""
            try:
                # Integration with shipping APIs (FedEx, UPS, etc.)
                shipping_options = [
                    {
                        "service": "Standard",
                        "cost": 5.99,
                        "delivery": "3-5 business days",
                        "free_over": 35,
                    },
                    {
                        "service": "Express",
                        "cost": 12.99,
                        "delivery": "1-2 business days",
                        "free_over": 100,
                    },
                    {
                        "service": "Overnight",
                        "cost": 24.99,
                        "delivery": "Next business day",
                        "free_over": 200,
                    },
                ]

                options_text = []
                for option in shipping_options:
                    cost = (
                        "FREE"
                        if cart_total >= option["free_over"]
                        else f"${option['cost']}"
                    )
                    options_text.append(
                        f"• {option['service']}: {cost} - {option['delivery']}"
                    )

                return f"🚚 Shipping to {zip_code}:\n" + "\n".join(options_text)

            except Exception as e:
                return f"❌ Shipping check error: {str(e)}"

    async def initialize_agent(self):
        """Initialize the production e-commerce agent"""

        ecommerce_agent = OmniCoreAgent(
            name="ProductionEcommerceShopper",
            system_instruction="""You are a professional e-commerce personal shopper in a production environment.

CORE RESPONSIBILITIES:
1. PERSONALIZATION: Learn user preferences and provide tailored recommendations
2. PRODUCT DISCOVERY: Help users find products using search and filters
3. CART MANAGEMENT: Assist with adding/removing items and viewing cart
4. INVENTORY CHECKING: Verify stock levels and availability
5. PRICE COMPARISON: Show competitive pricing when relevant
6. SHIPPING INFORMATION: Provide delivery options and times

WORKFLOW PRIORITIES:
- FIRST: Understand user needs and preferences
- Use search_products for product discovery
- Check inventory before recommending
- Save user preferences for future personalization
- Provide price comparisons for expensive items
- Always show shipping options at checkout

TONE: Helpful, knowledgeable, personalized, and sales-oriented but not pushy.""",
            model_config={
                "provider": "openai",
                "model": "gpt-4",
                "temperature": 0.3,
                "max_context_length": 6000,
            },
            local_tools=self.tool_registry,
            agent_config={
                "max_steps": 15,
                "tool_call_timeout": 30,
                "request_limit": 100,
                "memory_config": {"mode": "sliding_window", "value": 200},
                "enable_tools_knowledge_base": True,
            },
            debug=False,
        )

        return ecommerce_agent

    async def handle_shopping_request(
        self, user_message: str, user_id: str = None, session_id: str = None
    ):
        """Process a shopping request with personalized service"""
        agent = await self.initialize_agent()

        if not session_id:
            session_id = f"shop_{int(datetime.now().timestamp())}"

        if user_id:
            # Add user context to the message
            user_context = ""
            if user_id in self.user_profiles:
                prefs = self.user_profiles[user_id]["preferences"]
                user_context = (
                    f" [User {user_id} - Preferences: {prefs.get('categories', [])}]"
                )

            user_message = user_message + user_context

        try:
            result = await agent.run(user_message, session_id=session_id)
            return {
                "success": True,
                "response": result.get(
                    "response", "I'd be happy to help with your shopping needs!"
                ),
                "session_id": session_id,
                "user_id": user_id,
                "cart_updated": "cart" in user_message.lower()
                or "add" in user_message.lower(),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "session_id": session_id,
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
            }


# Production Usage Example
async def production_ecommerce_demo():
    """Demo the production e-commerce agent with real shopping scenarios"""

    print("🛍️ PRODUCTION E-COMMERCE PERSONAL SHOPPER")
    print("=" * 60)

    ecommerce_agent = ProductionEcommerceAgent()

    # Real shopping scenarios
    test_scenarios = [
        {
            "user": "customer123",
            "message": "I'm looking for wireless headphones under $150",
            "type": "Product Search",
        },
        {
            "user": "customer123",
            "message": "Can you show me what's in my cart?",
            "type": "Cart View",
        },
        {
            "user": "customer456",
            "message": "I prefer electronics and my budget is $200-500. I like Sony and Apple brands.",
            "type": "Preference Setting",
        },
        {
            "user": "customer456",
            "message": "What recommendations do you have for me?",
            "type": "Personalized Recommendations",
        },
        {
            "user": "customer789",
            "message": "Add 2 Sony wireless headphones to my cart and check shipping to 94102",
            "type": "Cart + Shipping",
        },
    ]

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n🛒 Scenario {i}: {scenario['type']}")
        print(f"👤 User: {scenario['user']}")
        print(f"💬 Query: {scenario['message']}")
        print("-" * 50)

        result = await ecommerce_agent.handle_shopping_request(
            user_message=scenario["message"],
            user_id=scenario["user"],
            session_id=f"shop_session_{i}",
        )

        if result["success"]:
            print(f"🤖 Shopping Assistant: {result['response']}")
            if result["cart_updated"]:
                print("📝 Note: Shopping cart was updated")
        else:
            print(f"❌ Error: {result['error']}")

        print(f"🆔 Session: {result['session_id']}")
        print(f"⏰ Time: {result['timestamp']}")

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(production_ecommerce_demo())
