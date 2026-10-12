"""A small products API on Ferro's ORM.

ferro migrate --database-url sqlite:///shop.db
ferro dev --database-url sqlite:///shop.db
"""

from decimal import Decimal, InvalidOperation

from ferro import App, HTTPException, Response, models

app = App()


class Product(models.Model):
    name = models.String(max_length=200, index=True)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)


def parse_price(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, "price must be a decimal number") from None


@app.post("/products")
async def create_product(request):
    data = await request.json()
    product = await Product.objects.create(
        name=data["name"], price=parse_price(data["price"]), stock=int(data.get("stock", 0))
    )
    return Response(product.to_dict(), status=201, headers={"location": f"/products/{product.pk}"})


@app.get("/products")
async def list_products(min_price: str | None = None, page: int = 1, per_page: int = 20):
    products = Product.objects.filter(active=True).order_by("name")
    if min_price is not None:
        products = products.filter(price__gte=parse_price(min_price))
    start = (max(page, 1) - 1) * per_page
    return {
        "count": await products.count(),
        "results": [p.to_dict() for p in await products[start : start + per_page]],
    }


@app.get("/products/{id}")
async def get_product(id: int):
    return (await Product.objects.get_or_404(id=id)).to_dict()


@app.patch("/products/{id}")
async def update_product(id: int, request):
    product = await Product.objects.get_or_404(id=id)
    data = await request.json()
    if "price" in data:
        product.price = parse_price(data["price"])
    for field in ("name", "stock", "active"):
        if field in data:
            setattr(product, field, data[field])
    await product.save()
    return product.to_dict()


@app.delete("/products/{id}")
async def delete_product(id: int):
    deleted = await Product.objects.filter(id=id).delete()
    if not deleted:
        raise HTTPException(404, "Product not found")
