"""Database models of the shop.

After changing them run `ferro makemigrations` and `ferro migrate`.
"""

from datetime import UTC, datetime
from decimal import Decimal

from ferro import models
from ferro.auth import User


def now() -> datetime:
    return datetime.now(UTC)


class Category(models.Model):
    name = models.String(max_length=100)
    slug = models.String(max_length=100, unique=True)

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.String(max_length=200)
    slug = models.String(max_length=200, unique=True)
    description = models.Text(default="")
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)
    created = models.DateTime(default=now)

    def __str__(self) -> str:
        return self.name


class Order(models.Model):
    PENDING, PAID, CANCELLED = "pending", "paid", "cancelled"

    customer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders")
    status = models.String(max_length=20, default=PENDING, index=True)
    total = models.Decimal(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    created = models.DateTime(default=now)
    updated = models.DateTime(default=now)

    def __str__(self) -> str:
        return f"Order {self.pk}"


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_lines")
    quantity = models.Integer()
    unit_price = models.Decimal(max_digits=10, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.quantity} x product {self.product_id}"


class Payment(models.Model):
    APPROVED, DECLINED = "approved", "declined"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    amount = models.Decimal(max_digits=12, decimal_places=2)
    status = models.String(max_length=20)
    card_last4 = models.String(max_length=4)
    reference = models.String(max_length=40, unique=True)
    created = models.DateTime(default=now)

    def __str__(self) -> str:
        return self.reference
