# Feature Specification: ORM Completeness

**Feature Branch**: `022-orm-completeness`

**Created**: 2026-10-20

**Status**: Implemented

**Input**: "https://docs.djangoproject.com/en/6.1/topics/db/queries/ … read this; does my
framework need any update?" followed by "yes do" for the gaps listed in the comparison.

## Clarifications

### Session 2026-10-20

Recommended answers recorded (the user approved the whole list):

- Q: Which lookups? → A: `istartswith`, `iendswith`, `range`, `regex`, `iregex`; `__in` takes a
  queryset (one column) as a subquery. Regular expressions use PostgreSQL's syntax there and
  Rust's `regex` crate on SQLite (registered as SQLite's `REGEXP` function).
- Q: Date transforms? → A: `year`, `quarter`, `month`, `day`, `week_day` (1 = Sunday),
  `hour`, `minute`, `second`, `date`, followed by any comparison lookup. Date-times are read in
  UTC on both databases (PostgreSQL sessions may use another time zone).
- Q: JSON queries? → A: key paths with numeric array indexes, `has_key`, `has_keys`,
  `has_any_keys`, `contains` (PostgreSQL `@>` semantics; on SQLite through a registered
  function), `isnull` on a key = missing or JSON null. Keys are bound parameters.
- Q: Expressions? → A: `annotate()` takes `Value`, database functions (`Lower`, `Upper`,
  `Trim`, `Length`, `Abs`, `Concat`, `Coalesce`, `Greatest`, `Least`, `Now`), `Case`/`When`,
  `Subquery`, `Exists` with `OuterRef`; strings in functions name fields (as in Django).
  `F()` gains `%`; `Q` gains `^`.
- Q: QuerySet helpers? → A: `distinct()`, `none()`, `get_or_create()` (race-safe through a
  savepoint and a second read), `update_or_create()` (row lock), `in_bulk()`, `explain()`.
- Q: Raw SQL? → A: `db.execute()`, `db.fetch()`, `Model.objects.raw()`; `%s` placeholders only,
  translated to the database's; values are always parameters; inside `transaction()` they join
  it.
- Q: Reverse foreign keys? → A: `add()` for any key; `remove()`, `clear()`, `set()` only for
  nullable keys (as in Django).
- Q: `delete()` returning per-model counts? → A: Not changed (would break the `int` return).

## Requirements *(mandatory)*

- **FR-001**: New lookups and queryset `__in` operands.
- **FR-002**: Date transforms.
- **FR-003**: JSON key-path queries and JSON-only lookups.
- **FR-004**: Expression annotations including subqueries.
- **FR-005**: QuerySet helpers.
- **FR-006**: Parameterized raw SQL.
- **FR-007**: Reverse foreign-key manager methods.
- **FR-008**: Documentation.

## Success Criteria *(mandatory)*

- **SC-001**: Every requirement is tested on SQLite and PostgreSQL; the full suite passes.
- **SC-002**: No value ever appears in SQL text (keys and patterns included).
