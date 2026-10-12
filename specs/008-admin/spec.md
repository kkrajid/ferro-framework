# Feature Specification: Admin

**Feature Branch**: `008-admin`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 008 from docs/roadmap.md (vision Phase 7, §16): `from
ferro.admin import admin; admin.register(Product)` generates `/admin` with list, search, filter,
create, edit, delete per model, using the same ORM and auth system."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: How is the admin rendered? → A: Server-rendered HTML pages with plain forms and no
  JavaScript, styled by one small stylesheet. Nothing to build or bundle, works with the
  cross-origin protection and a strict Content-Security-Policy, and keeps the "no frontend
  framework" boundary of the MVP.
- Q: Who may use it? → A: Active staff users. Each model page also checks the model's
  `view_`/`add_`/`change_`/`delete_<table>` permission (superusers have all). Anonymous visitors
  are redirected to the admin's own login page.
- Q: How is it added to an app? → A: `app.add_admin(admin)` (default prefix `/admin`), which also
  installs the auth middleware. `admin.register(Model, ModelAdmin(...))` customises a model;
  `admin.register(Model)` uses defaults.
- Q: How are relations edited? → A: Foreign keys as a select list of the related rows (by their
  `__str__`), many-to-many as a multi-select; lists with more than 500 related rows fall back to
  a key input.
- Q: How big can list pages get? → A: 50 rows per page by default (`list_per_page`), with
  previous/next links and a total count.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register models and browse them (Priority: P1)

```python
from ferro.admin import ModelAdmin, admin

admin.register(Category)
admin.register(Product, ModelAdmin(
    list_display=["name", "price", "category"],
    search_fields=["name", "category__name"],
    list_filter=["category", "active"],
    ordering=["-price"],
))
app.add_admin(admin)
```

**Why this priority**: Looking at and finding data is the first thing teams use an admin for.

**Independent Test**: Register models, log in as staff, open the index and list pages and check
columns, search, filters, ordering and pagination on both databases.

**Acceptance Scenarios**:

1. **Given** registered models, **Then** `/admin` lists them (only those the user may view).
2. **Given** a model list page, **Then** it shows `list_display` columns (default: `__str__` plus
   the first fields), with a link to each row, the total and page links.
3. **Given** `?q=phone`, **Then** rows matching any `search_fields` (case-insensitive contains)
   are shown; **Given** filter links (`?category=3`, `?active=true`), **Then** rows are filtered;
   **Given** `?o=-price`, **Then** rows are ordered.
4. **Given** values containing HTML (`<script>`), **Then** they are shown escaped.

---

### User Story 2 - Create, edit and delete (Priority: P1)

**Why this priority**: Fixing data without writing code is the admin's main value.

**Acceptance Scenarios**:

1. **Given** the add form, **Then** it has an input per editable field (text, textarea, number,
   checkbox, date/time, select for foreign keys, multi-select for many-to-many); submitting valid
   data creates the row and redirects to its page with a confirmation.
2. **Given** invalid data (missing required field, too long, not a number, duplicate unique
   value), **Then** the form is shown again with the entered values and a message per field;
   nothing is saved.
3. **Given** the change form, **Then** it is pre-filled; saving updates the row (and its
   many-to-many links); `readonly_fields` are shown but not editable.
4. **Given** delete, **Then** a confirmation page lists what will be deleted (cascades named);
   confirming deletes; a `PROTECT`ed delete shows an explanation instead.

---

### User Story 3 - Access control (Priority: P1)

**Acceptance Scenarios**:

1. **Given** an anonymous visitor, **Then** every admin URL redirects to `/admin/login?next=...`;
   the login form logs staff users in and redirects back; non-staff and wrong passwords get an
   error message; the login route is rate limited.
2. **Given** a staff user without `change_product`, **Then** product change forms are refused
   (403 page) and the list shows no edit links; without `view_product` the model is hidden.
3. **Given** a cross-site form post to the admin, **Then** it is refused (Feature 006).
4. **Given** admin pages, **Then** they send a strict Content-Security-Policy and `no-store`
   caching.

### Edge Cases

- Unregistered model or unknown key in the URL: 404 page.
- Model with a non-integer primary key (UUID, string): URLs and forms work.
- Decimal, date, time, timezone-aware datetime and JSON values round-trip through forms.
- Very large tables: list pages query only one page plus a count.
- Registering the same model twice: configuration error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ferro.admin.admin` (a default site) with `register(Model, ModelAdmin=None)`;
  `App.add_admin(site, prefix="/admin")`.
- **FR-002**: Index, list (display columns, search, filters, ordering, pagination), add, change,
  delete-confirmation pages, and login/logout, rendered as escaped HTML.
- **FR-003**: Forms generated from field kinds and relations, with server-side validation through
  the ORM's conversion rules and unique-constraint errors shown per field.
- **FR-004**: Staff-only access with per-model view/add/change/delete permissions.
- **FR-005**: `request.form()` for `application/x-www-form-urlencoded` bodies (repeated keys
  kept), available to all handlers.
- **FR-006**: Strict CSP and `cache-control: no-store` on admin pages; the login route is rate
  limited (10/minute per client).

## Success Criteria *(mandatory)*

- **SC-001**: Every acceptance scenario passes on SQLite and PostgreSQL.
- **SC-002**: A list page of a 10,000-row table renders with at most 4 queries (session, page,
  count, filter options) in under 100 ms.
- **SC-003**: No admin page contains user data unescaped (tested with hostile values in every
  field kind).

## Assumptions

- Inline editing of related rows, bulk actions, history/audit log and custom admin views are out
  of scope for the MVP.
- One admin site per app.
