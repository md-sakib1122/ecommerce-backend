"""Order API tests: create / read / cancel, plus the auth guard.

Money rules under test come from `OrderService`: prices are snapshotted from
the live product, subtotals/totals are computed server-side with Decimal, and
stock is NOT reserved at creation — it is only reduced when payment succeeds.
"""
from decimal import Decimal

from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import ProductStatus
from app.models.user import User


def _money(value) -> Decimal:
    """Normalize a JSON money field (string or number) for comparison."""
    return Decimal(str(value))


async def _seed_foreign_order(session, product) -> Order:
    """A pending order owned by a different user than the `client` fixture's."""
    other = User(email="other@example.com", hashed_password="x")
    session.add(other)
    await session.commit()
    await session.refresh(other)

    foreign = Order(
        user_id=other.id, status=OrderStatus.pending, total_amount=Decimal("10.00")
    )
    foreign.items = [
        OrderItem(
            product_id=product.id,
            quantity=1,
            price=Decimal("10.00"),
            subtotal=Decimal("10.00"),
        )
    ]
    session.add(foreign)
    await session.commit()
    await session.refresh(foreign)
    return foreign


# --- create --------------------------------------------------------------------
async def test_create_order_success(client, session, user, product):
    resp = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": product.id, "quantity": 2}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user_id"] == user.id
    assert body["status"] == "pending"
    assert _money(body["total_amount"]) == Decimal("20.00")
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["product_id"] == product.id
    assert item["quantity"] == 2
    assert _money(item["price"]) == Decimal("10.00")
    assert _money(item["subtotal"]) == Decimal("20.00")

    await session.refresh(product)
    assert product.stock == 5  # no reservation at creation


async def test_create_order_aggregates_duplicate_products(client, product):
    resp = await client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"product_id": product.id, "quantity": 1},
                {"product_id": product.id, "quantity": 2},
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 3
    assert _money(body["total_amount"]) == Decimal("30.00")


async def test_create_order_missing_product_404(client):
    resp = await client.post(
        "/api/v1/orders", json={"items": [{"product_id": 9999, "quantity": 1}]}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Product 9999 not found."


async def test_create_order_inactive_product_400(client, session, product):
    product.status = ProductStatus.inactive
    session.add(product)
    await session.commit()

    resp = await client.post(
        "/api/v1/orders", json={"items": [{"product_id": product.id, "quantity": 1}]}
    )
    assert resp.status_code == 400, resp.text
    assert (
        resp.json()["detail"] == f"Product {product.id} is not available for purchase."
    )


async def test_create_order_insufficient_stock_400(client, product):
    resp = await client.post(
        "/api/v1/orders", json={"items": [{"product_id": product.id, "quantity": 6}]}
    )
    assert resp.status_code == 400, resp.text
    assert "Insufficient stock" in resp.json()["detail"]


async def test_create_order_empty_items_422(client):
    resp = await client.post("/api/v1/orders", json={"items": []})
    assert resp.status_code == 422, resp.text


async def test_create_order_zero_quantity_422(client, product):
    resp = await client.post(
        "/api/v1/orders", json={"items": [{"product_id": product.id, "quantity": 0}]}
    )
    assert resp.status_code == 422, resp.text


async def test_order_price_is_snapshotted(client, session, product):
    resp = await client.post(
        "/api/v1/orders", json={"items": [{"product_id": product.id, "quantity": 2}]}
    )
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["id"]

    product.price = Decimal("99.00")
    session.add(product)
    await session.commit()

    resp = await client.get(f"/api/v1/orders/{order_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _money(body["items"][0]["price"]) == Decimal("10.00")
    assert _money(body["total_amount"]) == Decimal("20.00")


# --- read ----------------------------------------------------------------------
async def test_get_order_owner_200(client, order, product):
    resp = await client.get(f"/api/v1/orders/{order.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == order.id
    assert body["status"] == "pending"
    assert _money(body["total_amount"]) == Decimal("20.00")
    assert [item["product_id"] for item in body["items"]] == [product.id]


async def test_get_order_missing_404(client):
    resp = await client.get("/api/v1/orders/9999")
    assert resp.status_code == 404, resp.text


async def test_get_foreign_order_404(client, session, product):
    """Another user's order must 404 (not 403) so its existence never leaks."""
    foreign = await _seed_foreign_order(session, product)

    resp = await client.get(f"/api/v1/orders/{foreign.id}")
    assert resp.status_code == 404, resp.text


# --- cancel --------------------------------------------------------------------
async def test_cancel_pending_order_200(client, session, order):
    resp = await client.post(f"/api/v1/orders/{order.id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "canceled"

    await session.refresh(order)
    assert order.status == OrderStatus.canceled


async def test_cancel_is_idempotent(client, order):
    for _ in range(2):
        resp = await client.post(f"/api/v1/orders/{order.id}/cancel")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "canceled"


async def test_cancel_paid_order_409(client, session, order):
    order.status = OrderStatus.paid
    session.add(order)
    await session.commit()

    resp = await client.post(f"/api/v1/orders/{order.id}/cancel")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Only pending orders can be canceled."


async def test_cancel_foreign_order_404(client, session, product):
    foreign = await _seed_foreign_order(session, product)

    resp = await client.post(f"/api/v1/orders/{foreign.id}/cancel")
    assert resp.status_code == 404, resp.text


# --- auth guard ----------------------------------------------------------------
async def test_orders_require_auth_401(anon_client):
    body = {"items": [{"product_id": 1, "quantity": 1}]}
    assert (await anon_client.post("/api/v1/orders", json=body)).status_code == 401
    assert (await anon_client.get("/api/v1/orders/1")).status_code == 401
    assert (await anon_client.post("/api/v1/orders/1/cancel")).status_code == 401
