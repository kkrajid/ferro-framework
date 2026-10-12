"""Admin tests: models, a site factory and a logged-in client (both databases)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

from ferro import App, models
from ferro.admin import AdminSite, ModelAdmin
from ferro.auth import Group, Permission, Session, User
from tests.python.orm.conftest import database, postgres_server, use_models  # noqa: F401


class Category(models.Model):
    name = models.String(max_length=50)

    class Meta:
        table = "ad8_category"

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    name = models.String(max_length=20, unique=True)

    class Meta:
        table = "ad8_tag"

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    name = models.String(max_length=50)
    price = models.Decimal(max_digits=8, decimal_places=2)
    active = models.Boolean(default=True)
    note = models.Text(null=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    tags = models.ManyToMany(Tag, related_name="products")

    class Meta:
        table = "ad8_product"

    def __str__(self) -> str:
        return self.name


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    stars = models.Integer()

    class Meta:
        table = "ad8_review"


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Ticket(models.Model):
    code = models.UUID(primary_key=True, default=new_uuid)
    title = models.String(max_length=30)
    opened = models.DateTime(null=True)
    due = models.Date(null=True)
    data = models.JSON(null=True)

    class Meta:
        table = "ad8_ticket"


MODELS = (Permission, Group, User, Session, Category, Tag, Product, Review, Ticket)
PASSWORD = "admin password"


def make_site(**product_options) -> AdminSite:
    site = AdminSite()
    site.register(Category)
    site.register(Tag)
    site.register(
        Product,
        ModelAdmin(
            list_display=["name", "price", "category", "active"],
            search_fields=["name", "category__name"],
            list_filter=["category", "active"],
            readonly_fields=["note"],
            **product_options,
        ),
    )
    site.register(Review)
    site.register(Ticket)
    return site


async def shop() -> dict:
    await use_models(*MODELS)
    phones = await Category.objects.create(name="Phones")
    books = await Category.objects.create(name="Books")
    sale = await Tag.objects.create(name="sale")
    x1 = await Product.objects.create(name="X1", price=Decimal("499.00"), category=phones)
    await Product.objects.create(name="Novel", price=Decimal("12.50"), category=books, active=False)
    await Product.objects.create(name="X2", price=Decimal("899.00"), category=phones)
    await x1.tags.add(sale)
    await User.objects.create_superuser("boss", PASSWORD)
    return {"phones": phones, "books": books, "sale": sale, "x1": x1}


def logged_in(serve: Callable, site: AdminSite | None = None, username: str = "boss"):
    app = App()
    app.add_admin(site or make_site())
    client = serve(app, debug=True)
    response = client.post(
        "/admin/login", data={"username": username, "password": PASSWORD, "next": "/admin"}
    )
    assert response.status_code == 303, response.text
    return client
