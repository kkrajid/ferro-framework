//! Python classes the bridge needs, imported once per interpreter on first use.

use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyType;

pub(crate) struct Types {
    pub(crate) response: Py<PyType>,
    pub(crate) json_response: Py<PyType>,
    pub(crate) http_exception: Py<PyType>,
    pub(crate) validation_error: Py<PyType>,
    pub(crate) cancelled_error: Py<PyType>,
    pub(crate) format_exception: Py<PyAny>,
    pub(crate) eager_task_factory: Py<PyAny>,
    pub(crate) datetime_types: [Py<PyType>; 3],
    pub(crate) decimal: Py<PyType>,
    pub(crate) uuid: Py<PyType>,
}

static TYPES: PyOnceLock<Types> = PyOnceLock::new();

fn class(py: Python<'_>, module: &str, name: &str) -> PyResult<Py<PyType>> {
    Ok(py
        .import(module)?
        .getattr(name)?
        .cast_into::<PyType>()?
        .unbind())
}

pub(crate) fn types(py: Python<'_>) -> PyResult<&Types> {
    TYPES.get_or_try_init(py, || {
        let asyncio = py.import("asyncio")?;
        Ok(Types {
            response: class(py, "ferro.response", "Response")?,
            json_response: class(py, "ferro.response", "JSONResponse")?,
            http_exception: class(py, "ferro.exceptions", "HTTPException")?,
            validation_error: class(py, "ferro.exceptions", "ValidationError")?,
            cancelled_error: asyncio
                .getattr("CancelledError")?
                .cast_into::<PyType>()?
                .unbind(),
            format_exception: py
                .import("traceback")?
                .getattr("format_exception")?
                .unbind(),
            eager_task_factory: asyncio.getattr("eager_task_factory")?.unbind(),
            datetime_types: [
                class(py, "datetime", "datetime")?,
                class(py, "datetime", "date")?,
                class(py, "datetime", "time")?,
            ],
            decimal: class(py, "decimal", "Decimal")?,
            uuid: class(py, "uuid", "UUID")?,
        })
    })
}
