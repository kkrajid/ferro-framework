# Research: Admin

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

## R1. Rendering

- **Decision**: `ferro.admin` renders HTML with a tiny internal helper (`html(tag, attrs,
  children)` + `escape`) instead of a template engine: pages are small and fixed, everything is
  escaped by construction (SC-003), and no dependency is added. One stylesheet is served from
  `/admin/static/admin.css` (cacheable); pages carry `content-security-policy: default-src 'none';
  style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`
  and `cache-control: no-store`.
- **Alternatives rejected**: Jinja2 (dependency, autoescape must be configured), a JS SPA
  (frontend framework is out of MVP scope, more attack surface).

## R2. Routes

`GET /admin/` index · `GET|POST /admin/login` · `POST /admin/logout` ·
`GET /admin/static/admin.css` · `GET /admin/{model}/` list · `GET|POST /admin/{model}/add` ·
`GET|POST /admin/{model}/{pk}/change` · `GET|POST /admin/{model}/{pk}/delete`. `{model}` is the
table name. Routes are registered on the app by `add_admin`, so they go through the normal
engine (limits, CSRF origin check, rate limit on login).

## R3. Forms

- Field kind → widget: String (`input maxlength`), Text/JSON (`textarea`), Integer/BigInteger
  (`input type=number step=1`), Float/Decimal (`input type=number step=any`), Boolean
  (checkbox; absent = false), Date/Time (`type=date|time`), DateTime (`type=datetime-local`,
  interpreted as UTC and shown as UTC), UUID (text), ForeignKey (select, empty option when
  nullable), ManyToMany (multi-select). Auto primary keys are not editable.
- Parsing: strings from the form are converted in Python per kind (`int`, `Decimal`,
  `date.fromisoformat`, ...); conversion errors and missing required values are collected per
  field; the ORM validates the rest on save (length, digits) and `IntegrityError.fields` maps
  unique violations to fields. Saving the row and its many-to-many links happens in one
  `transaction()`.
- `request.form()` (new, all handlers): parses urlencoded bodies in Rust with
  `percent-encoding`, returns a `FormData` mapping with `get`/`getlist`.

## R4. Lists

- Search: `Q(field__icontains=q) | ...` over `search_fields` (relation paths allowed).
- Filters: `list_filter` fields; Boolean → yes/no links; ForeignKey → links for each related row
  (limited to 100); other kinds → distinct values (limited to 100) via `values_list(...).distinct`
  — not available, so `values(field).annotate(n=Count())` gives distinct values with counts.
- Pagination: `count()` + slice; `select_related` for foreign keys in `list_display`.

## R5. Permissions and login

- Staff check on every admin route; per-model codes `<action>_<table>`; superusers pass.
- Anonymous → `303 /admin/login?next=<path>` (next validated to stay under the prefix).
- Login uses `authenticate` + `login`; route rate limit `10/minute`.

## R6. Delete confirmation

Lists the row and, for each reverse foreign key with `CASCADE`, the count of rows that will be
deleted with it (`RelatedManager.count()`); `PROTECT` relations with rows make the page explain
why deletion is refused.
