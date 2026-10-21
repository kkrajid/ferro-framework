"""HTML templates with Jinja2 (Feature 025; ``pip install "ferro-framework[templates]"``).

```python
from ferro.templating import Templates

templates = Templates("templates", app=app)


@app.get("/products/{id}")
async def product(request, id: int):
    item = await Product.objects.get_or_404(pk=id)
    return templates.render("product.html", {"product": item, "request": request})
```

Autoescaping is on for every template, so values are HTML-escaped unless marked safe;
``url_for(name, **params)`` is available in templates when ``app`` is given.
"""

from __future__ import annotations

import os
from typing import Any

from ferro.exceptions import ConfigurationError
from ferro.response import HeadersInput, HTMLResponse

__all__ = ["Templates"]


class Templates:
    """A directory of Jinja2 templates."""

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        app: Any = None,
        auto_reload: bool = True,
        **environment: Any,
    ) -> None:
        try:
            import jinja2
        except ImportError:
            raise ConfigurationError(
                'Templates need Jinja2: pip install "ferro-framework[templates]" (or jinja2)'
            ) from None
        environment.setdefault("autoescape", True)
        self.environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(os.fspath(directory)),
            auto_reload=auto_reload,
            **environment,
        )
        if app is not None:
            self.environment.globals["url_for"] = app.url_for

    def render_string(self, name: str, context: dict[str, Any] | None = None) -> str:
        return self.environment.get_template(name).render(context or {})

    def render(
        self,
        name: str,
        context: dict[str, Any] | None = None,
        *,
        status: int = 200,
        headers: HeadersInput = None,
    ) -> HTMLResponse:
        """The rendered template as an HTML response."""
        return HTMLResponse(self.render_string(name, context), status, headers)
