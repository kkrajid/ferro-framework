"""The built-in admin (Feature 008).

```python
from ferro.admin import ModelAdmin, admin

admin.register(Category)
admin.register(Product, ModelAdmin(list_display=["name", "price"], search_fields=["name"]))
app.add_admin(admin)        # /admin, for active staff users
```

Pages are server-rendered HTML with plain forms (no JavaScript), escaped by construction and
served with a strict Content-Security-Policy.
"""

from ferro.admin.options import ModelAdmin
from ferro.admin.site import AdminSite, admin

__all__ = ["AdminSite", "ModelAdmin", "admin"]
