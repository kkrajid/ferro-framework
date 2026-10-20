//! WebSocket connections and streaming bodies on the engine side (Feature 018).

use std::sync::Arc;
use std::time::{Duration, Instant};

use actix_web::HttpRequest;
use actix_ws::{AggregatedMessage, AggregatedMessageStream, CloseCode, CloseReason, Session};
use bytes::Bytes;
use ferro_core::socket::close_code;
use ferro_core::{
    ChunkSource, SocketCommand, SocketEvent, SocketEvents, SocketLink, SocketMessage, StreamAborted,
};
use futures_util::Stream;

use crate::lifecycle::Lifecycle;

/// How often the server pings an idle client.
pub(crate) const PING_INTERVAL: Duration = Duration::from_secs(20);
/// A client silent for this long (not even a pong) is considered gone.
pub(crate) const SILENCE_LIMIT: Duration = Duration::from_secs(60);

/// Whether the request asks to upgrade to the WebSocket protocol.
pub(crate) fn wants_upgrade(req: &HttpRequest) -> bool {
    req.headers()
        .get("upgrade")
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.eq_ignore_ascii_case("websocket"))
}

fn reason(code: u16, description: &str) -> Option<CloseReason> {
    Some(CloseReason {
        code: CloseCode::from(code),
        description: (!description.is_empty()).then(|| description.to_string()),
    })
}

fn too_big(error: &actix_ws::ProtocolError) -> bool {
    match error {
        actix_ws::ProtocolError::Overflow => true,
        actix_ws::ProtocolError::Io(io) => io.to_string().contains("maximum continuation size"),
        _ => false,
    }
}

/// Runs one connection until it ends: frames from the client become events, commands from the
/// application become frames, idle clients are pinged, and a server stop closes with 1001.
pub(crate) async fn run(
    session: Session,
    mut frames: AggregatedMessageStream,
    link: Arc<SocketLink>,
    events: Arc<dyn SocketEvents>,
    lifecycle: Arc<Lifecycle>,
) {
    let _open = lifecycle.socket_opened();
    let mut ping = tokio::time::interval(PING_INTERVAL);
    ping.tick().await;
    let mut last_seen = Instant::now();
    let mut writer = session.clone();
    let (code, description) = loop {
        tokio::select! {
            biased;
            () = lifecycle.stopped() => {
                let text = "server shutting down";
                let _ = session.clone().close(reason(close_code::GOING_AWAY, text)).await;
                break (close_code::GOING_AWAY, text.to_string());
            }
            command = link.next_command() => match command {
                SocketCommand::Send(message) => {
                    let size = message.len();
                    let write = async {
                        match message {
                            SocketMessage::Text(text) => writer.text(text).await,
                            SocketMessage::Binary(bytes) => writer.binary(bytes).await,
                        }
                    };
                    // A client that stops reading must not hold the connection (and shutdown,
                    // pings, the application's close) for as long as TCP keeps trying.
                    match tokio::time::timeout(SILENCE_LIMIT, write).await {
                        Ok(Ok(())) => {}
                        Ok(Err(_)) => break (close_code::ABNORMAL, "connection lost".to_string()),
                        Err(_) => break (close_code::ABNORMAL, "client stopped reading".to_string()),
                    }
                    if link.sent(size) {
                        events.event(SocketEvent::Drained);
                    }
                }
                SocketCommand::Close { code, reason: text } => {
                    let _ = session.clone().close(reason(code, &text)).await;
                    break (code, text);
                }
            },
            frame = async {
                link.room_for_inbound().await;
                frames.recv().await
            } => match frame {
                Some(Ok(AggregatedMessage::Text(text))) => {
                    last_seen = Instant::now();
                    link.received(text.len());
                    events.event(SocketEvent::Message(SocketMessage::Text(text.to_string())));
                }
                Some(Ok(AggregatedMessage::Binary(bytes))) => {
                    last_seen = Instant::now();
                    link.received(bytes.len());
                    events.event(SocketEvent::Message(SocketMessage::Binary(bytes)));
                }
                Some(Ok(AggregatedMessage::Ping(payload))) => {
                    last_seen = Instant::now();
                    let _ = writer.pong(&payload).await;
                }
                Some(Ok(AggregatedMessage::Pong(_))) => last_seen = Instant::now(),
                Some(Ok(AggregatedMessage::Close(received))) => {
                    let (code, text) = received.as_ref().map_or((1005, String::new()), |r| {
                        (u16::from(r.code), r.description.clone().unwrap_or_default())
                    });
                    let _ = session.clone().close(received).await;
                    break (code, text);
                }
                Some(Err(error)) if too_big(&error) => {
                    let text = "message too big";
                    let _ = session.clone().close(reason(close_code::TOO_BIG, text)).await;
                    break (close_code::TOO_BIG, text.to_string());
                }
                Some(Err(error)) => {
                    let _ = session.clone().close(reason(1002, "protocol error")).await;
                    break (1002, error.to_string());
                }
                None => break (close_code::ABNORMAL, "connection lost".to_string()),
            },
            _ = ping.tick() => {
                if last_seen.elapsed() > SILENCE_LIMIT {
                    let _ = session.clone().close(None).await;
                    break (close_code::ABNORMAL, "keep-alive timeout".to_string());
                }
                let _ = writer.ping(b"").await;
            }
        }
    };
    link.mark_closed();
    events.event(SocketEvent::Closed {
        code,
        reason: description,
    });
}

/// The chunks of a streaming body. A body that cannot finish — the producer failed, or the server
/// stops first — ends with an error, so the client sees an incomplete response rather than a
/// complete-looking one. Dropping the stream (client gone) drops the source, which stops the
/// application's producer.
pub(crate) fn chunks(
    source: Box<dyn ChunkSource>,
    lifecycle: Arc<Lifecycle>,
) -> impl Stream<Item = Result<Bytes, StreamAborted>> {
    let open = lifecycle.stream_opened();
    futures_util::stream::unfold(Some((source, lifecycle, open)), |state| async move {
        let (source, lifecycle, open) = state?;
        loop {
            let chunk = tokio::select! {
                biased;
                () = lifecycle.stopped() => Err(StreamAborted),
                chunk = source.next_chunk() => chunk,
            };
            match chunk {
                Ok(Some(chunk)) if chunk.is_empty() => continue,
                Ok(Some(chunk)) => return Some((Ok(chunk), Some((source, lifecycle, open)))),
                Ok(None) => return None,
                Err(aborted) => {
                    // actix drops unwritten output when a body fails: let it write the
                    // chunks already produced first. The source is dropped here.
                    tokio::task::yield_now().await;
                    return Some((Err(aborted), None));
                }
            }
        }
    })
}
