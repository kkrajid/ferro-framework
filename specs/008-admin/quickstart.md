# Quickstart & Validation: Admin

```python
# app.py
from ferro import App, models
from ferro.admin import ModelAdmin, admin


class Category(models.Model):
    name = models.String(max_length=100)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)
    active = models.Boolean(default=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")


admin.register(Category)
admin.register(Product, ModelAdmin(list_display=["name", "price", "category"],
                                   search_fields=["name"], list_filter=["category", "active"]))

app = App(database_url="sqlite:///shop.db")
app.add_admin(admin)
```

```bash
ferro makemigrations && ferro migrate
FERRO_SUPERUSER_PASSWORD="a-long-secret" ferro createsuperuser --username admin --no-input
ferro dev        # open http://127.0.0.1:8000/admin/
```

Automated: `pytest tests/python/admin`.
