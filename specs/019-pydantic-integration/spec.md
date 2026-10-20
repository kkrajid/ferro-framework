# Feature Specification: Pydantic Integration

**Feature Branch**: `019-pydantic-integration`

**Created**: 2026-10-12

**Status**: Draft

**Input**: User description: "ithille serializer, deserializer like features undeeo? … pydantic use
cheyyan pattumoo ferrok … pydantic compatible akkiyaa mathi" — Ferro has no serializer layer;
instead of building its own, make Ferro work natively with Pydantic models for request bodies
and responses.

## Clarifications

### Session 2026-10-12

- Q: Ferro's own serializer classes (like DRF) or Pydantic? → A: Pydantic only (the user's
  decision). No Ferro schema classes.
- Q: Is Pydantic required? → A: No. It is an optional extra (`pip install ferro[pydantic]`,
  Pydantic ≥ 2.7, v2 only). Ferro never imports Pydantic itself; the integration activates when a
  handler's annotations use Pydantic models. Applications without Pydantic are unaffected.
- Q: How does a handler receive a validated body? → A: A parameter annotated with a Pydantic
  model (`data: ProductIn`) receives the request body validated into that model. JSON bodies are
  validated straight from the bytes (Pydantic's JSON parser); `application/x-www-form-urlencoded`
  bodies are validated from the form fields (repeated keys become lists). At most one body
  parameter per handler. `data: ProductIn | None = None` makes the body optional.
- Q: Error responses? → A: The same `422` format as path/query problems, one detail per Pydantic
  error: `{"param": "items.0.name", "location": "body", "problem": "<Pydantic message>"}`; path,
  query and body problems are reported together. A missing body for a required model is a `422`
  problem; malformed JSON is `400` (as `request.json()`); another content type is `415`.
- Q: Responses? → A: Returning a Pydantic model (anywhere in the returned value: alone, in a list,
  in a dict, in `JSONResponse`) serializes it as JSON in Pydantic's JSON mode. A return annotation
  that uses Pydantic models (`-> ProductOut`, `-> list[ProductOut]`) converts and filters what
  the handler returns — Ferro model instances included (read by attribute) — so only the
  declared fields are sent. `Response` objects returned by such a handler are sent unchanged.
  A returned value that does not fit the annotation is a server error (500, logged).
- Q: Ferro models? → A: No automatic Pydantic models from Ferro models in this feature; schemas
  read Ferro instances with `from_attributes`. Relations must be loaded (`select_related`,
  `prefetch_related`) for nested schemas; an unloaded relation is reported clearly.
- Q: Other places JSON is produced? → A: Pydantic models are accepted wherever Ferro encodes JSON:
  `ws.send_json`, `ferro.broadcast.publish`, the cache, `json_dumps`.
- Q: WebSockets? → A: No body parameters on WebSocket handlers (configuration error); use
  `Model.model_validate_json(await ws.receive())`.
- Q: OpenAPI? → A: Out of scope (a later feature can generate it from these annotations).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Validated request bodies (Priority: P1)

A developer declares the shape of a request body as a Pydantic model and receives a validated
object, with clear 422 errors for bad input.

**Independent Test**: `POST` valid and invalid JSON to a handler with `data: ProductIn`; the
handler gets a `ProductIn`; invalid input gets 422 with one detail per field.

**Acceptance Scenarios**:

1. **Given** `async def create(data: ProductIn)`, **When** a valid JSON body is posted, **Then**
   `data` is a `ProductIn` instance.
2. **Given** invalid fields (nested too), **Then** 422 with `location: "body"` and the dotted field
   path in `param`; path/query problems appear in the same response.
3. **Given** an empty body, **Then** 422 "missing request body"; with `data: ProductIn | None =
   None`, the handler gets `None`.
4. **Given** malformed JSON, **Then** 400; **given** `content-type: text/plain`, **Then** 415.
5. **Given** a form post, **Then** the model is validated from the form fields.
6. **Given** a plain `def` handler, **Then** the body is validated before the handler runs on the
   worker thread.
7. **Given** two model parameters, or a model parameter on a WebSocket handler, **Then** a
   configuration error at registration.

---

### User Story 2 - Pydantic responses (Priority: P1)

A developer returns Pydantic models, or declares a response model so that ORM objects are sent
with exactly the declared fields.

**Independent Test**: A handler annotated `-> ProductOut` returns a Ferro model instance; the
response contains only `ProductOut`'s fields.

**Acceptance Scenarios**:

1. **Given** a handler returning a `BaseModel` (or a list/dict containing them), **Then** the
   response is its JSON (Decimal as string, datetimes ISO 8601) with `application/json`.
2. **Given** `-> ProductOut` and a returned Ferro model instance, **Then** only declared fields
   are sent; `-> list[ProductOut]` works with a QuerySet result list.
3. **Given** `-> ProductOut` and a returned `Response`/`JSONResponse`, **Then** it is sent as is.
4. **Given** a returned value that does not fit, **Then** 500 and an error log naming the handler.
5. **Given** an unloaded relation in a nested schema, **Then** the error names the relation and
   how to load it.

---

### User Story 3 - Pydantic everywhere JSON is produced (Priority: P2)

**Acceptance Scenarios**:

1. **Given** `ws.send_json(model)`, `broadcast.publish(channel, model)`, `cache.set(key, model)`
   or `JSONResponse(model, status=201)`, **Then** the model is encoded as its JSON.

### Edge Cases

- Without Pydantic installed, nothing changes; annotations that are not Pydantic models keep the
  existing rules (unsupported annotations are still configuration errors).
- Bodies are limited by `max_body_size` before validation.
- `RootModel` and generic models work like other models.

## Requirements *(mandatory)*

- **FR-001**: A handler parameter annotated with a Pydantic model (or `Model | None` with a
  default) receives the validated body.
- **FR-002**: JSON bodies validated from bytes; form bodies from fields; 415 for other content
  types; 400 for malformed JSON; 422 for missing/invalid bodies, in Ferro's existing format,
  merged with path/query problems.
- **FR-003**: At most one body parameter; none on WebSocket handlers.
- **FR-004**: The JSON encoder accepts Pydantic models (JSON mode) wherever Ferro encodes JSON.
- **FR-005**: A return annotation using Pydantic models validates the returned value
  (`from_attributes`) and sends the JSON of the result; `Response` objects pass through; a
  mismatch is a 500 with an error log.
- **FR-006**: Pydantic is an optional extra (`ferro[pydantic]`); Ferro does not import it.
- **FR-007**: Documentation: a guide section (bodies, responses, ORM objects, errors, testing) and
  the changelog/stability policy.

## Success Criteria *(mandatory)*

- **SC-001**: The tutorial's create/update/list endpoints can be written with Pydantic schemas
  and no manual validation code.
- **SC-002**: Routes without Pydantic annotations have unchanged performance; a body-model route
  adds no more than Pydantic's own validation time.
- **SC-003**: Every invalid field of a body is reported in one 422 response.

## Assumptions

- Pydantic v1 is not supported. Query-parameter models, OpenAPI generation and automatic schemas
  from Ferro models are out of scope.
