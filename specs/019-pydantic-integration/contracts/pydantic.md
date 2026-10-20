# Contract: Pydantic Integration

No new public names. Behaviour active only for annotations that use Pydantic (v2) models.

## Body parameters

| Handler signature | Request | Result |
|---|---|---|
| `data: M` | JSON body (no content type, `application/json`, `*+json`) | `M.model_validate_json(body)` |
| `data: M` | `application/x-www-form-urlencoded` | `M.model_validate(fields)`; fields annotated `list`/`set`/`frozenset`/`tuple` (also `X \| None`) take `getlist`, others the last value; keys are field aliases or names |
| `data: M` | empty body | 422 `{"param": "data", "location": "body", "problem": "missing request body"}` |
| `data: M \| None = None` | empty body | `data is None` (the default) |
| `data: M` | malformed JSON | 400 `Malformed JSON body: …` |
| `data: M` | other content type | 415 `Expected a JSON or form body for 'data', got <type>` |
| `data: M` | invalid | 422, one detail per Pydantic error: `param` = dotted `loc` (parameter name when empty), `location` = `"body"`, `problem` = Pydantic `msg`; preceded by path/query problems |

Registration errors (`ConfigurationError`): two model parameters; a model parameter on a
WebSocket handler; a path parameter annotated with a model.

## Return annotations

If the return annotation mentions a Pydantic model (`M`, `list[M]`, `M | None`, `dict[str, M]`,
…; `Response` classes are removed from unions):

| Handler returns | Sent |
|---|---|
| `None` | 204 |
| a `ferro.Response` (any subclass) | unchanged |
| anything else | `TypeAdapter(annotation).validate_python(value, from_attributes=True)` → `Response(adapter.dump_json(...), media_type="application/json")` |
| a value that does not validate | 500; `TypeError` logged: `handler <name> returned a value that does not match its return annotation <annotation>: <Pydantic errors>` plus, for related managers that were not loaded, `<Model>.<relation> is not loaded; use .prefetch_related(...)` |

## JSON encoding

Wherever Ferro encodes JSON (handler results, `Response`/`JSONResponse` content, `ws.send_json`,
`broadcast.publish`, the cache, `json_dumps`), an object whose class has
`__pydantic_serializer__` and a `model_dump_json` method is written as `model_dump_json()`.

## Related managers

`RelatedManager` and `ManyRelatedManager` support iteration: the objects cached by
`prefetch_related`/`fetch_related`, or `RelatedNotLoaded` (never a query).

## Testing

`TestClient.request/get/post/put/patch/delete/options(..., content=bytes | str | None)` sends a
raw body (`str` as UTF-8); `content` wins over `json`/`data`.
