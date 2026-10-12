"""Ferro hello world. Run with `ferro dev` from this directory."""

import time

from ferro import App, HTTPException, Response

app = App()


@app.get("/")
async def home():
    return {"message": "Hello from Rust"}


@app.get("/products/{id}")
async def product(request, id: int, currency: str = "INR"):
    return {"id": id, "currency": currency, "path": request.path}


@app.get("/users/me")
async def me():
    return {"user": "me"}


@app.get("/users/{id}")
async def user(id: int):
    return {"user": id}


@app.post("/products")
async def create_product(request):
    data = await request.json()
    return Response(data, status=201, headers={"location": "/products/1"})


@app.get("/orders/{id}")
async def order(id: int):
    raise HTTPException(404, "Order not found")


@app.get("/boom")
async def boom():
    raise ValueError("something broke")


@app.get("/sync-sleep")
def sync_sleep():
    time.sleep(1)
    return {"slept": True}
