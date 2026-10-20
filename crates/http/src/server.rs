use std::fmt;
use std::io;
use std::net::{SocketAddr, TcpListener, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};

use crate::lifecycle::{Lifecycle, ShutdownStats};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use actix_web::dev::ServerHandle;
use actix_web::{App, HttpServer, web};
use ferro_core::security::{Policy, RouteOptions};
use ferro_core::{Dispatcher, Settings, SettingsError};
use ferro_router::Router;
use socket2::{Domain, Protocol, Socket, Type};

use crate::service::{State, handle};

const LISTEN_BACKLOG: i32 = 1024;

#[derive(Debug)]
pub enum StartError {
    Settings(SettingsError),
    Bind { address: String, source: io::Error },
    Engine(io::Error),
}

impl fmt::Display for StartError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Settings(err) => err.fmt(f),
            Self::Bind { address, source } => write!(f, "could not bind to {address}: {source}"),
            Self::Engine(err) => write!(f, "server failed to start: {err}"),
        }
    }
}

impl std::error::Error for StartError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Settings(err) => Some(err),
            Self::Bind { source, .. } => Some(source),
            Self::Engine(err) => Some(err),
        }
    }
}

impl From<SettingsError> for StartError {
    fn from(err: SettingsError) -> Self {
        Self::Settings(err)
    }
}

/// A server running on its own thread. Stop it with [`RunningServer::stop`] and wait for the
/// thread with [`RunningServer::wait`].
pub struct RunningServer {
    addr: SocketAddr,
    handle: ServerHandle,
    draining: Arc<AtomicBool>,
    lifecycle: Arc<Lifecycle>,
    finished: Arc<(Mutex<bool>, Condvar)>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl fmt::Debug for RunningServer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RunningServer")
            .field("addr", &self.addr)
            .finish_non_exhaustive()
    }
}

impl RunningServer {
    pub fn local_addr(&self) -> SocketAddr {
        self.addr
    }

    /// Readiness reports `server` from now on; requests are still served (Feature 011).
    pub fn begin_drain(&self) {
        self.draining.store(true, Ordering::Release);
    }

    /// What the shutdown did with the requests in flight; final once [`Self::wait`] returns
    /// `true`.
    pub fn shutdown_stats(&self) -> ShutdownStats {
        self.lifecycle.stats()
    }

    /// Requests shutdown and returns immediately. A graceful stop stops accepting connections and
    /// lets in-flight requests finish within the shutdown timeout.
    pub fn stop(&self, graceful: bool) {
        self.draining.store(true, Ordering::Release);
        self.lifecycle.begin_stop();
        // The stop command is sent eagerly; the returned future only reports completion, which
        // `wait` observes instead.
        drop(self.handle.stop(graceful));
    }

    /// Blocks until the server has stopped or `timeout` elapses. Returns `true` once stopped.
    pub fn wait(&self, timeout: Option<Duration>) -> bool {
        let (lock, condvar) = &*self.finished;
        let mut done = lock.lock().unwrap_or_else(|e| e.into_inner());
        let deadline = timeout.map(|t| Instant::now() + t);
        while !*done {
            match deadline {
                None => done = condvar.wait(done).unwrap_or_else(|e| e.into_inner()),
                Some(deadline) => {
                    let now = Instant::now();
                    if now >= deadline {
                        break;
                    }
                    done = condvar
                        .wait_timeout(done, deadline - now)
                        .unwrap_or_else(|e| e.into_inner())
                        .0;
                }
            }
        }
        let stopped = *done;
        drop(done);
        if stopped
            && let Some(thread) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take()
        {
            let _ = thread.join();
        }
        stopped
    }
}

/// Binds the listening socket and starts the server on a dedicated thread.
pub fn start(
    settings: Settings,
    router: Router,
    dispatcher: Arc<dyn Dispatcher>,
) -> Result<RunningServer, StartError> {
    start_with(settings, router, Vec::new(), dispatcher)
}

/// Like [`start`], with per-route security options indexed by route id.
pub fn start_with(
    settings: Settings,
    router: Router,
    routes: Vec<RouteOptions>,
    dispatcher: Arc<dyn Dispatcher>,
) -> Result<RunningServer, StartError> {
    settings.validate()?;
    let listener = bind(&settings.host, settings.port)?;
    start_on(settings, listener, router, routes, dispatcher)
}

/// Serves on an already listening socket (one shared by several worker processes, Feature 010).
pub fn start_on(
    settings: Settings,
    listener: TcpListener,
    router: Router,
    routes: Vec<RouteOptions>,
    dispatcher: Arc<dyn Dispatcher>,
) -> Result<RunningServer, StartError> {
    settings.validate()?;
    let policy = Policy::new(&settings, routes)?;
    let tls = crate::tls::server_config(&settings)?;
    listener.set_nonblocking(true).map_err(StartError::Engine)?;
    let addr = listener.local_addr().map_err(StartError::Engine)?;

    let draining = Arc::new(AtomicBool::new(false));
    let lifecycle = Arc::new(Lifecycle::default());
    let thread_lifecycle = lifecycle.clone();
    let enabled = |path: &str| (!path.is_empty()).then(|| path.to_string());
    let metrics = if settings.metrics {
        let allow = ferro_core::security::proxy::TrustedProxies::parse(&settings.metrics_allow)
            .map_err(|message| {
                StartError::Settings(ferro_core::SettingsError::new("metrics_allow", message))
            })?;
        let mut metrics =
            crate::metrics::Metrics::new(&router, settings.metrics_path.clone(), allow);
        if let Some(dir) = &settings.metrics_dir {
            metrics.snapshots = Some(crate::snapshots::Snapshots::new(dir).map_err(|err| {
                StartError::Settings(ferro_core::SettingsError::new(
                    "metrics_dir",
                    format!("cannot use '{dir}': {err}"),
                ))
            })?);
        }
        Some(metrics)
    } else {
        None
    };
    let state = Arc::new(State {
        router,
        dispatcher,
        max_body_size: settings.max_body_size,
        request_timeout: settings.request_timeout,
        policy,
        health_path: enabled(&settings.health_path),
        ready_path: enabled(&settings.ready_path),
        draining: draining.clone(),
        lifecycle: lifecycle.clone(),
        metrics,
        tls: tls.is_some(),
    });
    let workers = settings.workers;
    let per_worker_connections = settings.max_connections.div_ceil(workers).max(1);
    let header_timeout = settings.header_timeout;
    let shutdown_secs = settings.shutdown_timeout.as_secs_f64().ceil() as u64;

    let publisher = Publisher::start(&state);
    let finished = Arc::new((Mutex::new(false), Condvar::new()));
    let finished_signal = finished.clone();
    let (handle_tx, handle_rx) = mpsc::channel::<io::Result<ServerHandle>>();

    let thread = std::thread::Builder::new()
        .name("ferro-server".into())
        .spawn(move || {
            let system = actix_rt::System::new();
            system.block_on(async move {
                let server = HttpServer::new(move || {
                    App::new()
                        .app_data(web::Data::from(state.clone()))
                        .default_service(web::to(handle))
                })
                .workers(workers)
                .max_connections(per_worker_connections)
                .client_request_timeout(header_timeout)
                // A client that closes its side while its request runs is gone: end the
                // connection, which drops the handler (cancelling it and rolling back its
                // transaction) instead of finishing work nobody will read (Feature 011).
                .h1_allow_half_closed(false)
                // A TLS handshake gets as long as a client gets to send its headers.
                .tls_handshake_timeout(header_timeout)
                .shutdown_timeout(shutdown_secs)
                .disable_signals();
                let server = match tls {
                    // ALPN offers `h2` and `http/1.1`; the client chooses.
                    Some(config) => server.listen_rustls_0_23(listener, config),
                    None => server.listen(listener),
                };
                match server {
                    Ok(server) => {
                        let server = server.run();
                        let _ = handle_tx.send(Ok(server.handle()));
                        if let Err(err) = server.await {
                            tracing::error!(target: "ferro.server", "server stopped with error: {err}");
                        }
                    }
                    Err(err) => {
                        let _ = handle_tx.send(Err(err));
                    }
                }
            });
            summarize_shutdown(&thread_lifecycle);
            if let Some(publisher) = publisher {
                publisher.finish();
            }
            let (lock, condvar) = &*finished_signal;
            *lock.lock().unwrap_or_else(|e| e.into_inner()) = true;
            condvar.notify_all();
        })
        .map_err(StartError::Engine)?;

    match handle_rx.recv() {
        Ok(Ok(handle)) => Ok(RunningServer {
            addr,
            handle,
            draining,
            lifecycle,
            finished,
            thread: Mutex::new(Some(thread)),
        }),
        Ok(Err(err)) => {
            let _ = thread.join();
            Err(StartError::Engine(err))
        }
        Err(_) => {
            let _ = thread.join();
            Err(StartError::Engine(io::Error::other(
                "server thread exited during startup",
            )))
        }
    }
}

/// Publishes this worker's metrics for the other workers every second, and once more when the
/// server stops (Feature 015).
struct Publisher {
    stop: mpsc::Sender<()>,
    thread: JoinHandle<()>,
}

impl Publisher {
    fn start(state: &Arc<State>) -> Option<Self> {
        state.metrics.as_ref()?.snapshots.as_ref()?;
        let state = state.clone();
        let (stop, stopped) = mpsc::channel::<()>();
        let thread = std::thread::Builder::new()
            .name("ferro-metrics".into())
            .spawn(move || {
                let publish = || {
                    let metrics = state.metrics.as_ref().expect("metrics are on");
                    let text = metrics.render(&state.lifecycle, state.dispatcher.as_ref());
                    if let Some(snapshots) = &metrics.snapshots
                        && let Err(err) = snapshots.publish(&text)
                    {
                        tracing::debug!(target: "ferro.server", "metrics snapshot failed: {err}");
                    }
                };
                loop {
                    publish();
                    match stopped.recv_timeout(crate::snapshots::INTERVAL) {
                        Err(mpsc::RecvTimeoutError::Timeout) => {}
                        _ => break,
                    }
                }
                publish();
            })
            .ok()?;
        Some(Self { stop, thread })
    }

    fn finish(self) {
        let _ = self.stop.send(());
        let _ = self.thread.join();
    }
}

/// Logs what the shutdown did. Engine workers drop abandoned requests just after the server
/// future completes, so this waits briefly for the in-flight count to settle first.
fn summarize_shutdown(lifecycle: &Lifecycle) {
    if !lifecycle.stopping() {
        return;
    }
    let deadline = Instant::now() + Duration::from_secs(1);
    while lifecycle.in_flight() > 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    let stats = lifecycle.stats();
    tracing::info!(
        target: "ferro.server",
        "stopped: {} requests in flight at stop, {} finished, {} cut off after shutdown_timeout",
        stats.at_stop,
        stats.finished,
        stats.cut_off,
    );
}

fn bind(host: &str, port: u16) -> Result<TcpListener, StartError> {
    let address = format_address(host, port);
    let bind_error = |source: io::Error| StartError::Bind {
        address: address.clone(),
        source,
    };
    let addr = (host, port)
        .to_socket_addrs()
        .map_err(bind_error)?
        .next()
        .ok_or_else(|| bind_error(io::Error::new(io::ErrorKind::NotFound, "no address found")))?;
    let socket = Socket::new(Domain::for_address(addr), Type::STREAM, Some(Protocol::TCP))
        .map_err(bind_error)?;
    // On Windows SO_REUSEADDR lets another process steal the port, so it is only set elsewhere.
    #[cfg(not(windows))]
    socket.set_reuse_address(true).map_err(bind_error)?;
    socket.bind(&addr.into()).map_err(bind_error)?;
    socket.listen(LISTEN_BACKLOG).map_err(bind_error)?;
    socket.set_nonblocking(true).map_err(bind_error)?;
    Ok(socket.into())
}

fn format_address(host: &str, port: u16) -> String {
    if host.contains(':') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    }
}
