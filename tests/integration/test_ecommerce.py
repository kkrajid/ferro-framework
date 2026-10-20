"""Feature 012 — MVP acceptance: the e-commerce example built and run with Ferro's commands.

Starts from `ferro new ecommerce`, copies the example's files over it, then runs the Definition
of Done sequence (makemigrations, migrate, createsuperuser, run) and the shopping journey against
a real server, on SQLite and PostgreSQL. Also runs the project's own tests.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from tests.support.postgres import free_port, temporary_cluster

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "ecommerce"
PROJECT_FILES = ["app.py", "models.py", "admin.py", "README.md", "pyproject.toml"]
SEED = """
from decimal import Decimal
phones = await Category.objects.create(name="Phones", slug="phones")
books = await Category.objects.create(name="Books", slug="books")
for name, slug, category, price, stock in [
    ("Phone X", "phone-x", phones, "499.00", 10),
    ("Rust Book", "rust-book", books, "39.90", 50),
    ("Last Phones", "last-phones", phones, "199.00", 5),
]:
    await Product.objects.create(
        name=name, slug=slug, category=category, price=Decimal(price), stock=stock
    )
"""


def environment(url: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    env["FERRO_DATABASE_URL"] = url
    return env


def ferro(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "ferro", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"ferro {' '.join(args)}:\n{result.stdout}\n{result.stderr}"
    return result


def create_project(base: Path) -> Path:
    """`ferro new ecommerce`, then the example's own files on top (as a developer would)."""
    subprocess.run(
        [sys.executable, "-m", "ferro", "new", "ecommerce"],
        cwd=base,
        check=True,
        capture_output=True,
        timeout=120,
    )
    project = base / "ecommerce"
    for name in PROJECT_FILES:
        shutil.copy(EXAMPLE / name, project / name)
    (project / "tests" / "test_app.py").unlink()
    shutil.copy(EXAMPLE / "tests" / "test_shop.py", project / "tests" / "test_shop.py")
    shutil.rmtree(project / "migrations")
    shutil.copytree(EXAMPLE / "migrations", project / "migrations")
    return project


class Shop:
    def __init__(self, project: Path, env: dict[str, str]):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = (project / "server.log").open("wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self.process = subprocess.Popen(
            # `ferro dev` as in the Definition of Done (`ferro run` sets HTTPS-only cookies).
            [sys.executable, "-m", "ferro", "dev", "--no-reload", "--port", str(self.port)],
            cwd=project,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self.log,
            creationflags=flags,
        )
        deadline = time.monotonic() + 60
        while True:
            assert self.process.poll() is None, (project / "server.log").read_text()
            try:
                if httpx.get(f"{self.base}/readyz", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "the shop never became ready"
            time.sleep(0.1)

    def stop(self) -> int:
        if sys.platform == "win32":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.process.send_signal(signal.SIGTERM)
        code = self.process.wait(60)
        self.log.close()
        return code


def customer(base: str, username: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=30)
    password = f"{username}-password-1"
    assert (
        client.post("/api/register", json={"username": username, "password": password}).status_code
        == 201
    )
    assert (
        client.post("/api/login", json={"username": username, "password": password}).status_code
        == 200
    )
    return client


def shopping_journey(base: str) -> None:
    products = {p["slug"]: p for p in httpx.get(f"{base}/api/products").json()["results"]}
    assert set(products) == {"phone-x", "rust-book", "last-phones"}
    alice = customer(base, "alice")
    try:
        assert alice.get("/api/me").json()["username"] == "alice"
        order = alice.post(
            "/api/orders",
            json={
                "items": [
                    {"product_id": products["phone-x"]["id"], "quantity": 2},
                    {"product_id": products["rust-book"]["id"], "quantity": 3},
                ]
            },
        )
        assert order.status_code == 201, order.text
        order = order.json()
        assert order["total"] == "1117.70" and order["status"] == "pending"
        paid = alice.post(
            f"/api/orders/{order['id']}/pay", json={"card_number": "4242424242424242"}
        )
        assert paid.status_code == 201 and paid.json()["status"] == "approved"
        assert alice.get(f"/api/orders/{order['id']}").json()["status"] == "paid"
        assert httpx.get(f"{base}/api/products/phone-x").json()["stock"] == 8
        assert httpx.get(f"{base}/api/products/rust-book").json()["stock"] == 47
        assert httpx.get(f"{base}/api/orders").status_code == 401
    finally:
        alice.close()

    # SC-003: 20 customers race for the last 5 items.
    clients = [customer(base, f"racer{i}") for i in range(20)]
    try:
        item = {"items": [{"product_id": products["last-phones"]["id"], "quantity": 1}]}
        with ThreadPoolExecutor(20) as pool:
            statuses = list(
                pool.map(lambda c: c.post("/api/orders", json=item).status_code, clients)
            )
    finally:
        for client in clients:
            client.close()
    assert sorted(statuses) == [201] * 5 + [409] * 15, statuses
    assert httpx.get(f"{base}/api/products/last-phones").json()["stock"] == 0


def concurrent_mixed_orders(base: str) -> None:
    """Orders for two products listed in opposite orders, some cancelled, all at once: no
    deadlock or server error, and stock adds up exactly (Feature 020)."""
    products = {p["slug"]: p for p in httpx.get(f"{base}/api/products").json()["results"]}
    first, second = products["phone-x"], products["rust-book"]
    slugs = ("phone-x", "rust-book")
    before = {s: httpx.get(f"{base}/api/products/{s}").json()["stock"] for s in slugs}
    clients = [customer(base, f"mixed{i}") for i in range(16)]

    def shop(i: int) -> tuple[int, int | None]:
        items = [
            {"product_id": first["id"], "quantity": 1},
            {"product_id": second["id"], "quantity": 2},
        ]
        if i % 2:
            items.reverse()
        placed = clients[i].post("/api/orders", json={"items": items})
        if placed.status_code == 201 and i % 4 == 0:
            cancelled = clients[i].post(f"/api/orders/{placed.json()['id']}/cancel")
            return placed.status_code, cancelled.status_code
        return placed.status_code, None

    try:
        with ThreadPoolExecutor(16) as pool:
            results = list(pool.map(shop, range(16)))
    finally:
        for client in clients:
            client.close()
    assert {placed for placed, _ in results} <= {201, 409}, results
    assert all(cancelled in (None, 200) for _, cancelled in results), results
    kept = sum(1 for placed, cancelled in results if placed == 201 and cancelled is None)
    after = {s: httpx.get(f"{base}/api/products/{s}").json()["stock"] for s in slugs}
    assert after == {
        "phone-x": before["phone-x"] - kept,
        "rust-book": before["rust-book"] - 2 * kept,
    }


def admin_back_office(base: str) -> None:
    with httpx.Client(base_url=base, timeout=30, follow_redirects=True) as staff:
        page = staff.post(
            "/admin/login",
            data={"username": "admin", "password": "admin-password-1", "next": "/admin"},
        )
        assert page.status_code == 200 and "Log out" in page.text, page.text[:300]
        products = staff.get("/admin/product")
        assert products.status_code == 200 and "Phone X" in products.text
        orders = staff.get("/admin/order?status=paid")
        assert orders.status_code == 200 and "paid" in orders.text


def definition_of_done(project: Path, url: str) -> None:
    env = environment(url)
    assert "No changes detected" in ferro(project, env, "makemigrations").stdout
    ferro(project, env, "migrate")
    ferro(
        project,
        {**env, "FERRO_SUPERUSER_PASSWORD": "admin-password-1"},
        "createsuperuser",
        "--username",
        "admin",
        "--no-input",
    )
    ferro(project, env, "shell", "-c", SEED)
    shop = Shop(project, env)
    try:
        shopping_journey(shop.base)
        concurrent_mixed_orders(shop.base)
        admin_back_office(shop.base)
    finally:
        code = shop.stop()
    assert code == 0, (project / "server.log").read_text()


def test_definition_of_done_on_sqlite(tmp_path):
    project = create_project(tmp_path)
    definition_of_done(project, f"sqlite:///{(project / 'shop.sqlite3').as_posix()}")


def test_definition_of_done_on_postgresql(tmp_path):
    with temporary_cluster() as server:
        if server is None:
            pytest.skip("PostgreSQL not available")
        server.execute("CREATE DATABASE shop")
        project = create_project(tmp_path)
        url = server.url.rsplit("/", 1)[0] + "/shop?sslmode=disable"
        definition_of_done(project, url)


def test_the_projects_own_tests_pass(tmp_path):
    project = create_project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=project,
        env={k: v for k, v in os.environ.items() if not k.startswith("FERRO_")},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
