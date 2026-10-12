"""Ferro's ORM: declare models, query them with an async QuerySet.

```python
from ferro import models

class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)

class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="orders")

products = await Product.objects.filter(price__gte=1000, category__name="Phones")
```
"""

from ferro.models.base import Model
from ferro.models.exceptions import (
    DatabaseError,
    DatabaseUnavailableError,
    FieldError,
    IntegrityError,
    MultipleObjectsReturned,
    ObjectDoesNotExist,
    OperationalError,
    ProtectedError,
    RelatedNotLoaded,
    TransactionError,
)
from ferro.models.expressions import Avg, Count, F, Max, Min, Q, Sum
from ferro.models.fields import (
    JSON,
    UUID,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Decimal,
    Field,
    Float,
    Integer,
    String,
    Text,
    Time,
)
from ferro.models.queryset import QuerySet
from ferro.models.relations import (
    CASCADE,
    DO_NOTHING,
    PROTECT,
    SET_NULL,
    ForeignKey,
    ManyToMany,
    OneToOne,
    prefetch_related_objects,
)

__all__ = [
    "CASCADE",
    "DO_NOTHING",
    "JSON",
    "PROTECT",
    "SET_NULL",
    "UUID",
    "Avg",
    "BigInteger",
    "Boolean",
    "Count",
    "DatabaseError",
    "DatabaseUnavailableError",
    "Date",
    "DateTime",
    "Decimal",
    "F",
    "Field",
    "FieldError",
    "Float",
    "ForeignKey",
    "Integer",
    "IntegrityError",
    "ManyToMany",
    "Max",
    "Min",
    "Model",
    "MultipleObjectsReturned",
    "ObjectDoesNotExist",
    "OneToOne",
    "OperationalError",
    "ProtectedError",
    "Q",
    "QuerySet",
    "RelatedNotLoaded",
    "String",
    "Sum",
    "Text",
    "Time",
    "TransactionError",
    "prefetch_related_objects",
]
