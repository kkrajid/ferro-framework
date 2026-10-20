//! Notifications (`LISTEN`/`NOTIFY`) for broadcast across processes and machines (Feature 018):
//! one dedicated connection per listener, reconnecting with backoff when it breaks.

use std::sync::Arc;
use std::time::Duration;

use ferro_database::DbError;
use tokio_postgres::AsyncMessage;

use crate::errors::connect_error;
use crate::worker::Shared;

/// A PostgreSQL identifier quoted for SQL text.
fn quote(identifier: &str) -> String {
    format!("\"{}\"", identifier.replace('"', "\"\""))
}

pub(crate) async fn run(
    shared: Arc<Shared>,
    channel: String,
    deliver: Arc<dyn Fn(String) + Send + Sync>,
) {
    let mut delay = Duration::from_millis(200);
    loop {
        match listen_once(&shared, &channel, deliver.as_ref()).await {
            // The connection ended after working: reconnect soon.
            Ok(()) => delay = Duration::from_millis(200),
            Err(err) => {
                tracing::warn!(target: "ferro.db", "broadcast listener: {err}; retrying in {delay:?}");
            }
        }
        tokio::time::sleep(delay).await;
        delay = (delay * 2).min(Duration::from_secs(10));
    }
}

async fn listen_once(
    shared: &Shared,
    channel: &str,
    deliver: &(dyn Fn(String) + Send + Sync),
) -> Result<(), DbError> {
    let (client, mut connection) = shared
        .config
        .driver
        .connect(shared.connector.clone())
        .await
        .map_err(|err| connect_error(&shared.config, &err))?;
    let (sender, mut received) = tokio::sync::mpsc::unbounded_channel::<String>();
    // The connection must be polled for `LISTEN` to complete and for notifications to arrive.
    let pump = tokio::spawn(async move {
        loop {
            match std::future::poll_fn(|cx| connection.poll_message(cx)).await {
                Some(Ok(AsyncMessage::Notification(notification))) => {
                    if sender.send(notification.payload().to_string()).is_err() {
                        break;
                    }
                }
                Some(Ok(_)) => {}
                Some(Err(_)) | None => break,
            }
        }
    });
    let listening = client
        .batch_execute(&format!("LISTEN {}", quote(channel)))
        .await
        .map_err(|err| DbError::Unavailable {
            message: format!("LISTEN failed: {err}"),
        });
    if let Err(err) = listening {
        pump.abort();
        return Err(err);
    }
    while let Some(payload) = received.recv().await {
        deliver(payload);
    }
    drop(client);
    pump.abort();
    Ok(())
}
