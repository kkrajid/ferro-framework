"""Feature 008 US2 — add, change and delete (both databases)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from tests.python.admin.conftest import (
    Category,
    Product,
    Review,
    Tag,
    Ticket,
    logged_in,
    shop,
)


async def test_add_form_widgets(database, serve):
    data = await shop()
    page = logged_in(serve).get("/admin/ad8_product/add")
    assert page.status_code == 200
    text = page.text
    assert 'name="name"' in text and 'maxlength="50"' in text
    assert 'name="price"' in text and 'type="number"' in text
    assert 'type="checkbox" name="active"' in text and "checked" in text
    assert '<select name="category"' in text
    assert f'<option value="{data["phones"].pk}">Phones</option>' in text
    assert '<select name="tags" multiple' in text
    assert 'name="note"' not in text, "readonly fields have no input"


async def test_create_with_relations(database, serve):
    data = await shop()
    client = logged_in(serve)
    response = client.post(
        "/admin/ad8_product/add",
        data={
            "name": "Tablet",
            "price": "299.99",
            "active": "on",
            "category": str(data["phones"].pk),
            "tags": [str(data["sale"].pk)],
        },
    )
    assert response.status_code == 303
    created = await Product.objects.get(name="Tablet")
    assert response.headers["location"] == f"/admin/ad8_product/{created.pk}/change"
    assert created.price == Decimal("299.99") and created.active
    assert created.category_id == data["phones"].pk
    assert [t.name for t in await created.tags.all()] == ["sale"]
    page = client.get(response.headers["location"])
    assert "was added" in page.text


async def test_invalid_input_is_shown_and_nothing_saved(database, serve):
    data = await shop()
    client = logged_in(serve)
    before = await Product.objects.count()
    page = client.post(
        "/admin/ad8_product/add",
        data={"name": "", "price": "abc", "category": str(data["phones"].pk)},
    )
    assert page.status_code == 200
    assert "This field is required" in page.text
    assert "Enter a number" in page.text
    assert 'value="abc"' in page.text, "entered values are kept"
    too_long = client.post(
        "/admin/ad8_product/add",
        data={"name": "x" * 60, "price": "1", "category": str(data["phones"].pk)},
    )
    assert "at most 50 characters" in too_long.text
    duplicate = client.post("/admin/ad8_tag/add", data={"name": "sale"})
    assert duplicate.status_code == 200 and "already exists" in duplicate.text
    assert await Product.objects.count() == before
    assert await Tag.objects.count() == 1


async def test_change_form_updates_and_keeps_readonly_fields(database, serve):
    data = await shop()
    x1 = data["x1"]
    await Product.objects.filter(pk=x1.pk).update(note="internal")
    client = logged_in(serve)
    page = client.get(f"/admin/ad8_product/{x1.pk}/change")
    assert 'value="X1"' in page.text and 'value="499.00"' in page.text
    assert "internal" in page.text
    response = client.post(
        f"/admin/ad8_product/{x1.pk}/change",
        data={
            "name": "X1 Pro",
            "price": "549.00",
            "category": str(data["books"].pk),
            "note": "hacked",
        },
    )
    assert response.status_code == 303
    changed = await Product.objects.get(pk=x1.pk)
    assert (changed.name, changed.price, changed.category_id) == (
        "X1 Pro",
        Decimal("549.00"),
        data["books"].pk,
    )
    assert changed.active is False, "an unchecked checkbox is False"
    assert changed.note == "internal", "readonly fields are not written"
    assert await changed.tags.count() == 0, "no tags selected removes the links"
    assert client.get("/admin/ad8_product/999999/change").status_code == 404
    assert client.get("/admin/nope").status_code == 404


async def test_delete_with_confirmation_cascade_and_protection(database, serve):
    data = await shop()
    x1 = data["x1"]
    await Review.objects.create(product=x1, stars=5)
    client = logged_in(serve)
    confirm = client.get(f"/admin/ad8_product/{x1.pk}/delete")
    assert "X1" in confirm.text and "1 Review" in confirm.text
    response = client.post(f"/admin/ad8_product/{x1.pk}/delete")
    assert response.status_code == 303 and response.headers["location"] == "/admin/ad8_product"
    assert await Product.objects.filter(pk=x1.pk).count() == 0
    assert await Review.objects.count() == 0
    protected = client.get(f"/admin/ad8_category/{data['phones'].pk}/delete")
    assert "cannot be deleted" in protected.text
    refused = client.post(f"/admin/ad8_category/{data['phones'].pk}/delete")
    assert refused.status_code == 200 and "cannot be deleted" in refused.text
    assert await Category.objects.filter(pk=data["phones"].pk).count() == 1


async def test_uuid_keys_and_value_round_trips(database, serve):
    await shop()
    client = logged_in(serve)
    response = client.post(
        "/admin/ad8_ticket/add",
        data={
            "title": "Broken",
            "opened": "2026-10-11T09:30",
            "due": "2026-10-20",
            "data": '{"priority": 1, "tags": ["a"]}',
        },
    )
    assert response.status_code == 303, response.text
    ticket = await Ticket.objects.get(title="Broken")
    assert isinstance(ticket.code, uuid.UUID)
    assert ticket.opened == datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
    assert ticket.due == date(2026, 10, 20)
    assert ticket.data == {"priority": 1, "tags": ["a"]}
    page = client.get(f"/admin/ad8_ticket/{ticket.code}/change")
    assert 'value="2026-10-11T09:30"' in page.text and 'value="2026-10-20"' in page.text
    bad = client.post(f"/admin/ad8_ticket/{ticket.code}/change", data={"title": "x", "data": "{"})
    assert "Enter valid JSON" in bad.text
    assert client.get("/admin/ad8_ticket/not-a-uuid/change").status_code == 404
