//! `ferro._ferro`: the native half of the Ferro Python package.
//!
//! Private to `ferro`. Request flow (see specs/001-runtime-core/contracts/rust-python-boundary.md):
//! the engine routes and reads the request, [`dispatch::PyDispatcher`] queues it on the
//! [`bridge`], whose drain on the Python execution thread builds the [`request::Request`], runs
//! the endpoint and lets [`convert`] / [`errors`] turn the outcome into a response.

mod bridge;
mod convert;
mod db;
mod dispatch;
mod errors;
mod json;
mod logging;
mod migrations;
mod orm;
mod orm_query;
mod orm_values;
mod passwords;
mod request;
mod server;
mod session;
mod types;

use pyo3::prelude::*;

#[pymodule]
fn _ferro(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<request::Request>()?;
    m.add_class::<request::QueryParams>()?;
    m.add_class::<request::Headers>()?;
    m.add_class::<request::Ready>()?;
    m.add_class::<server::Server>()?;
    m.add_function(wrap_pyfunction!(server::parse_path, m)?)?;
    m.add_function(wrap_pyfunction!(server::check_tls, m)?)?;
    m.add_function(wrap_pyfunction!(server::create_shared_state, m)?)?;
    m.add_function(wrap_pyfunction!(json::json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(json::json_loads, m)?)?;
    m.add_function(wrap_pyfunction!(logging::init_logging, m)?)?;
    m.add_function(wrap_pyfunction!(logging::log, m)?)?;
    // ORM (Feature 002)
    m.add_class::<orm::PyModelMeta>()?;
    m.add_class::<orm_query::PyQuery>()?;
    m.add_function(wrap_pyfunction!(orm::register_model, m)?)?;
    m.add_function(wrap_pyfunction!(orm::register_many_to_many, m)?)?;
    m.add_function(wrap_pyfunction!(orm::registered_models, m)?)?;
    m.add_function(wrap_pyfunction!(orm::insert, m)?)?;
    m.add_function(wrap_pyfunction!(orm::update_instance, m)?)?;
    m.add_function(wrap_pyfunction!(orm::delete_instance, m)?)?;
    m.add_function(wrap_pyfunction!(orm::bulk_insert, m)?)?;
    m.add_function(wrap_pyfunction!(orm::bulk_update, m)?)?;
    m.add_function(wrap_pyfunction!(orm::list_tables, m)?)?;
    m.add_function(wrap_pyfunction!(orm::create_tables, m)?)?;
    m.add_function(wrap_pyfunction!(db::db_connect, m)?)?;
    m.add_function(wrap_pyfunction!(db::db_disconnect, m)?)?;
    m.add_function(wrap_pyfunction!(db::db_is_connected, m)?)?;
    // Transactions (Feature 005)
    m.add_class::<session::Session>()?;
    m.add(
        "transaction_context",
        session::context(m.py())?.clone_ref(m.py()),
    )?;
    // Request ids in log records (Feature 011)
    m.add(
        "request_id_context",
        bridge::request_id_context(m.py())?.clone_ref(m.py()),
    )?;
    // Authentication (Feature 007)
    m.add_function(wrap_pyfunction!(passwords::hash_password, m)?)?;
    m.add_function(wrap_pyfunction!(passwords::check_password, m)?)?;
    // Migrations (Feature 004)
    m.add_function(wrap_pyfunction!(migrations::migration_diff, m)?)?;
    m.add_function(wrap_pyfunction!(migrations::migration_sql, m)?)?;
    m.add_function(wrap_pyfunction!(migrations::apply_migration, m)?)?;
    m.add_function(wrap_pyfunction!(migrations::ensure_migration_table, m)?)?;
    m.add_function(wrap_pyfunction!(migrations::migration_history, m)?)?;
    Ok(())
}
