"""Models shown in the admin at /admin (the shop's back office).

Log in with a user created by `ferro createsuperuser`.
"""

from models import Category, Order, OrderLine, Payment, Product

from ferro.admin import ModelAdmin, admin

admin.register(
    Category,
    ModelAdmin(list_display=["name", "slug"], search_fields=["name", "slug"], ordering=["name"]),
)
admin.register(
    Product,
    ModelAdmin(
        list_display=["name", "category", "price", "stock", "active"],
        search_fields=["name", "slug", "category__name"],
        list_filter=["category", "active"],
        ordering=["name"],
        readonly_fields=["created"],
    ),
)
admin.register(
    Order,
    ModelAdmin(
        list_display=["__str__", "customer", "status", "total", "created"],
        search_fields=["customer__username"],
        list_filter=["status"],
        ordering=["-created"],
        readonly_fields=["created", "updated"],
    ),
)
admin.register(
    OrderLine,
    ModelAdmin(list_display=["order", "product", "quantity", "unit_price"], ordering=["-order"]),
)
admin.register(
    Payment,
    ModelAdmin(
        list_display=["reference", "order", "amount", "status", "card_last4", "created"],
        search_fields=["reference"],
        list_filter=["status"],
        ordering=["-created"],
        readonly_fields=["created"],
    ),
)
