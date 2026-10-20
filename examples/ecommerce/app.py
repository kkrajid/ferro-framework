"""ecommerce: the Ferro MVP acceptance shop (Feature 012).

    ferro makemigrations && ferro migrate
    FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser --username admin --no-input
    ferro dev

JSON API under /api, admin at /admin.
"""

import secrets
from datetime import UTC, datetime
from decimal import Decimal

import admin  # noqa: F401 - registers the models with the admin site
from models import Category, Order, OrderLine, Payment, Product

from ferro import App, HTTPException, Response, auth
from ferro.auth import User, login_required, validate_password
from ferro.db import transaction
from ferro.models import F, Q

app = App()  # settings come from .env, FERRO_* variables and the command line
app.add_admin()  # also installs authentication: request.user and request.session

PAGE_SIZE = 20
MAX_QUANTITY = 100


def product_json(product: Product) -> dict:
    return {
        "id": product.pk,
        "slug": product.slug,
        "name": product.name,
        "description": product.description,
        "price": str(product.price),
        "stock": product.stock,
        "category_id": product.category_id,
    }


def order_json(order: Order, lines: list[OrderLine]) -> dict:
    return {
        "id": order.pk,
        "status": order.status,
        "total": str(order.total),
        "created": order.created.isoformat(),
        "lines": [
            {
                "product_id": line.product_id,
                "quantity": line.quantity,
                "unit_price": str(line.unit_price),
            }
            for line in lines
        ],
    }


async def body(request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(422, "Expected a JSON object")
    return data


# -- account --------------------------------------------------------------------------------------


@app.post("/api/register")
async def register(request):
    data = await body(request)
    username = str(data.get("username", "")).strip()
    password = data.get("password")
    if not username:
        raise HTTPException(422, "username is required")
    if not isinstance(password, str):
        raise HTTPException(422, "password is required")
    validate_password(password, username)
    if await User.objects.filter(username=username).exists():
        raise HTTPException(409, f"username {username!r} is already taken")
    user = await User.objects.create_user(username, password, email=str(data.get("email", "")))
    return Response({"id": user.pk, "username": user.username}, status=201)


@app.post("/api/login")
async def login(request):
    data = await body(request)
    user = await auth.authenticate(str(data.get("username", "")), str(data.get("password", "")))
    if user is None:
        raise HTTPException(401, "Invalid username or password")
    await auth.login(request, user)
    return {"id": user.pk, "username": user.username}


@app.post("/api/logout")
async def logout(request):
    await auth.logout(request)
    return {"ok": True}


@app.get("/api/me")
@login_required
async def me(request):
    return {"id": request.user.pk, "username": request.user.username}


# -- catalogue ------------------------------------------------------------------------------------


@app.get("/api/categories")
async def categories():
    return [
        {"id": c.pk, "slug": c.slug, "name": c.name}
        for c in await Category.objects.order_by("name")
    ]


@app.get("/api/products")
async def products(category: str | None = None, q: str | None = None, page: int = 1):
    if page < 1:
        raise HTTPException(422, "page must be 1 or more")
    found = Product.objects.filter(active=True)
    if category:
        found = found.filter(category__slug=category)
    if q:
        found = found.filter(Q(name__icontains=q) | Q(description__icontains=q))
    total = await found.count()
    start = (page - 1) * PAGE_SIZE
    items = await found.order_by("name", "pk")[start : start + PAGE_SIZE]
    return {
        "count": total,
        "page": page,
        "pages": max(1, -(-total // PAGE_SIZE)),
        "results": [product_json(p) for p in items],
    }


@app.get("/api/products/{slug}")
async def product(slug: str):
    return product_json(await Product.objects.get_or_404(slug=slug, active=True))


# -- orders ---------------------------------------------------------------------------------------


def parse_items(data: dict) -> dict[int, int]:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(422, "items must be a non-empty list of {product_id, quantity}")
    quantities: dict[int, int] = {}
    for item in items:
        product_id = item.get("product_id") if isinstance(item, dict) else None
        quantity = item.get("quantity") if isinstance(item, dict) else None
        if not isinstance(product_id, int) or isinstance(product_id, bool):
            raise HTTPException(422, "product_id must be an integer")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise HTTPException(422, "quantity must be an integer")
        if not 1 <= quantity <= MAX_QUANTITY:
            raise HTTPException(422, f"quantity must be between 1 and {MAX_QUANTITY}")
        quantities[product_id] = quantities.get(product_id, 0) + quantity
    return quantities


async def own_order(request, order_id: int) -> Order:
    order = await Order.objects.filter(pk=order_id, customer=request.user).first()
    if order is None:
        raise HTTPException(404, f"Order {order_id} not found")
    return order


@app.post("/api/orders")
@login_required
async def create_order(request):
    quantities = parse_items(await body(request))
    # One transaction: every line's stock goes down only while enough remains, or nothing does.
    # Every database round trip here holds the stock rows' locks, so there are as few as
    # possible: the order is inserted once with its total, after the stock is taken.
    async with transaction():
        # All products in one query; then one conditional stock update per product, in id
        # order so concurrent checkouts lock rows in the same order (no deadlocks).
        wanted = sorted(quantities)
        found = {p.pk: p for p in await Product.objects.filter(pk__in=wanted, active=True)}
        for product_id in wanted:
            if product_id not in found:
                raise HTTPException(404, f"Product {product_id} not found")
        total = Decimal("0.00")
        for product_id in wanted:
            quantity = quantities[product_id]
            taken = await Product.objects.filter(pk=product_id, stock__gte=quantity).update(
                stock=F("stock") - quantity
            )
            if not taken:
                raise HTTPException(409, f"Not enough stock for {found[product_id].name!r}")
            total += found[product_id].price * quantity
        order = await Order.objects.create(customer=request.user, total=total)
        lines = [
            OrderLine(
                order=order,
                product=found[product_id],
                quantity=quantities[product_id],
                unit_price=found[product_id].price,
            )
            for product_id in wanted
        ]
        if len(lines) == 1:
            await lines[0].save()
        else:
            await OrderLine.objects.bulk_create(lines)
    return Response(order_json(order, lines), status=201)


@app.get("/api/orders")
@login_required
async def my_orders(request):
    orders = await Order.objects.filter(customer=request.user).order_by("-created", "-pk")
    lines = await OrderLine.objects.filter(order__customer=request.user).order_by("pk")
    by_order: dict[int, list[OrderLine]] = {}
    for line in lines:
        by_order.setdefault(line.order_id, []).append(line)
    return [order_json(order, by_order.get(order.pk, [])) for order in orders]


@app.get("/api/orders/{order_id}")
@login_required
async def order_detail(request, order_id: int):
    order = await own_order(request, order_id)
    lines = await OrderLine.objects.filter(order=order).order_by("pk")
    return order_json(order, lines)


@app.post("/api/orders/{order_id}/cancel")
@login_required
async def cancel_order(request, order_id: int):
    async with transaction():
        order = await own_order(request, order_id)
        changed = await Order.objects.filter(pk=order.pk, status=Order.PENDING).update(
            status=Order.CANCELLED, updated=datetime.now(UTC)
        )
        if not changed:
            raise HTTPException(409, f"Order {order_id} is {order.status} and cannot be cancelled")
        lines = await OrderLine.objects.filter(order=order).order_by("pk")
        for line in sorted(lines, key=lambda line: line.product_id):
            await Product.objects.filter(pk=line.product_id).update(
                stock=F("stock") + line.quantity
            )
    order.status = Order.CANCELLED
    return order_json(order, lines)


@app.post("/api/orders/{order_id}/pay")
@login_required
async def pay_order(request, order_id: int):
    """Test payments: card numbers ending in an even digit are approved, others declined."""
    card = str((await body(request)).get("card_number", "")).replace(" ", "")
    if not card.isdigit() or not 12 <= len(card) <= 19:
        raise HTTPException(422, "card_number must be 12 to 19 digits")
    approved = int(card[-1]) % 2 == 0
    async with transaction():
        order = await own_order(request, order_id)
        if order.status != Order.PENDING:
            raise HTTPException(409, f"Order {order_id} is {order.status}")
        payment = await Payment.objects.create(
            order=order,
            amount=order.total,
            status=Payment.APPROVED if approved else Payment.DECLINED,
            card_last4=card[-4:],
            reference=f"pay_{secrets.token_hex(12)}",
        )
        if approved:
            paid = await Order.objects.filter(pk=order.pk, status=Order.PENDING).update(
                status=Order.PAID, updated=datetime.now(UTC)
            )
            if not paid:
                raise HTTPException(409, f"Order {order_id} is no longer pending")
    result = {
        "payment": payment.reference,
        "status": payment.status,
        "amount": str(payment.amount),
        "card_last4": payment.card_last4,
    }
    return Response(result, status=201 if approved else 402)
