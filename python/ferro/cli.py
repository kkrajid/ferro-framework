"""The ``ferro`` command: ``dev``, ``run`` and ``routes`` (contracts/cli.md)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ferro import __version__
from ferro.app import App
from ferro.config import Settings, resolve
from ferro.exceptions import ConfigurationError
from ferro.migrations.errors import MigrationError
from ferro.models.exceptions import DatabaseError

__all__ = ["load_app", "main"]

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_BIND = 3

DEFAULT_TARGET = "app:app"

_CANONICAL_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

# (option, setting, type) for options shared by `dev` and `run`.
_SERVER_OPTIONS: tuple[tuple[str, str, type], ...] = (
    ("--host", "host", str),
    ("--port", "port", int),
    ("--workers", "workers", int),
    ("--max-body-size", "max_body_size", int),
    ("--request-timeout", "request_timeout", float),
    ("--shutdown-timeout", "shutdown_timeout", float),
    ("--log-level", "log_level", str),
    ("--max-connections", "max_connections", int),
    ("--header-timeout", "header_timeout", float),
    ("--sync-threads", "sync_threads", int),
    ("--database-url", "database_url", str),
    ("--processes", "processes", int),
    ("--log-format", "log_format", str),
    ("--shutdown-drain", "shutdown_drain", float),
    ("--tls-cert", "tls_cert", str),
    ("--tls-key", "tls_key", str),
)


class _UserImportError(Exception):
    """The developer's module raised while importing; carries the formatted traceback."""


def _split_target(target: str) -> tuple[str, str]:
    module_name, sep, attribute = target.partition(":")
    if not sep or not module_name or not attribute:
        raise ConfigurationError(f"invalid target {target!r}: expected 'module:attribute'")
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    return module_name, attribute


def check_target(target: str) -> None:
    """Fails fast when the target's module does not exist, without running user code.

    Errors inside an existing module are left to the server process, so ``ferro dev`` can
    report them and retry after the next save.
    """
    module_name, _ = _split_target(target)
    try:
        spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        spec = None
    except Exception:
        return
    if spec is None:
        raise ConfigurationError(f"could not import module '{module_name}'")


def load_app(target: str) -> App:
    """Imports ``module:attribute`` with the current directory first on ``sys.path``."""
    module_name, attribute = _split_target(target)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and (module_name == exc.name or module_name.startswith(exc.name + ".")):
            raise ConfigurationError(f"could not import module '{module_name}'") from None
        raise _UserImportError(traceback.format_exc()) from None
    except Exception:
        raise _UserImportError(traceback.format_exc()) from None
    try:
        app = getattr(module, attribute)
    except AttributeError:
        raise ConfigurationError(f"module '{module_name}' has no attribute '{attribute}'") from None
    if not isinstance(app, App):
        raise ConfigurationError(f"'{target}' is a {type(app).__name__}, not a ferro.App instance")
    return app


def _add_server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
    for option, setting, kind in _SERVER_OPTIONS:
        parser.add_argument(option, dest=setting, type=kind, metavar=kind.__name__.upper())
    parser.add_argument("--debug", action=argparse.BooleanOptionalAction, default=None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ferro", description="Ferro application server")
    parser.add_argument("--version", action="version", version=f"Ferro {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    dev = commands.add_parser("dev", help="run the development server (debug, auto-reload)")
    _add_server_options(dev)
    dev.add_argument("--reload", action=argparse.BooleanOptionalAction, default=True)
    dev.add_argument(
        "--reload-dir", action="append", default=[], metavar="DIR", help="directory to watch"
    )

    run = commands.add_parser("run", help="run the production server")
    _add_server_options(run)

    routes = commands.add_parser("routes", help="list routes without starting a server")
    routes.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")

    database = commands.add_parser("db", help="database commands")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    create = database_commands.add_parser(
        "create-tables", help="create missing tables for the application's models"
    )
    create.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
    create.add_argument("--database-url", dest="database_url", metavar="URL")
    reset = database_commands.add_parser(
        "sequence-reset",
        help="move PostgreSQL primary key sequences past rows inserted with explicit ids",
    )
    reset.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
    reset.add_argument("--database-url", dest="database_url", metavar="URL")
    client = database_commands.add_parser("shell", help="open the database's own client")
    client.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
    client.add_argument("--database-url", dest="database_url", metavar="URL")

    new = commands.add_parser("new", help="create a new project")
    new.add_argument("name", help="project name, or . for the current (empty) directory")
    new.add_argument("--force", action="store_true", help="write into a non-empty directory")

    shell = commands.add_parser("shell", help="Python shell with the app, models and await")
    shell.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
    shell.add_argument("-c", dest="code", metavar="CODE", help="run CODE and exit")
    shell.add_argument("--database-url", dest="database_url", metavar="URL")

    def migration_options(command: argparse.ArgumentParser, database: bool = True) -> None:
        command.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
        command.add_argument("--migrations-dir", default="migrations", metavar="DIR")
        if database:
            command.add_argument("--database-url", dest="database_url", metavar="URL")

    make = commands.add_parser("makemigrations", help="write migrations for model changes")
    migration_options(make, database=False)
    make.add_argument("--name", help="migration name instead of an automatic one")
    make.add_argument("--check", action="store_true", help="exit 1 if changes are pending")
    make.add_argument("--empty", action="store_true", help="create an empty migration")

    migrate = commands.add_parser("migrate", help="apply unapplied migrations")
    migration_options(migrate)

    rollback = commands.add_parser("rollback", help="revert applied migrations")
    migration_options(rollback)
    group = rollback.add_mutually_exclusive_group()
    group.add_argument("--steps", type=int, default=1, metavar="N")
    group.add_argument("--to", metavar="NAME", help="keep NAME applied (zero reverts all)")

    migration = commands.add_parser("migration", help="migration information")
    migration_commands = migration.add_subparsers(dest="migration_command", required=True)
    migration_options(migration_commands.add_parser("status", help="list migrations"))

    superuser = commands.add_parser("createsuperuser", help="create an admin user")
    superuser.add_argument("target", nargs="?", default=DEFAULT_TARGET, help="module:attribute")
    superuser.add_argument("--username", required=True)
    superuser.add_argument("--email", default="")
    superuser.add_argument("--database-url", dest="database_url", metavar="URL")
    superuser.add_argument(
        "--no-input", action="store_true", help="never prompt (use FERRO_SUPERUSER_PASSWORD)"
    )
    return parser


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)


def _cli_settings(args: argparse.Namespace) -> dict[str, Any]:
    values = {setting: getattr(args, setting) for _, setting, _ in _SERVER_OPTIONS}
    values["debug"] = args.debug
    return values


def _child_argv(args: argparse.Namespace) -> list[str]:
    """Arguments that make a reload child serve exactly what the parent was asked to serve."""
    argv = ["dev", args.target, "--no-reload"]
    for option, setting, _ in _SERVER_OPTIONS:
        value = getattr(args, setting)
        if value is not None:
            argv += [option, str(value)]
    if args.debug is not None:
        argv.append("--debug" if args.debug else "--no-debug")
    return argv


def _single_process(args: argparse.Namespace) -> None:
    if args.processes is not None and args.processes > 1:
        raise ConfigurationError(
            "ferro dev runs a single process (auto-reload); use ferro run --processes N"
        )


def _serve(args: argparse.Namespace, mode: str) -> int:
    from ferro.server import serve

    if mode == "dev":
        _single_process(args)
    app = load_app(args.target)
    settings = resolve(cli=_cli_settings(args), app=app._settings, mode=mode)  # type: ignore[arg-type]
    if settings.database_url is not None:
        _warn_unapplied(settings.database_url)
    label = "development mode" if mode == "dev" else "production mode"
    if mode == "run" and settings.processes > 1:
        return _supervise(args, settings, label)
    banner = (
        f"Ferro {__version__} serving {args.target} on {{url}} "
        f"({label}, {settings.workers} workers)"
    )
    try:
        return serve(app, settings, banner=banner)
    except OSError as exc:
        _error(str(exc))
        return EXIT_BIND


def _supervise(args: argparse.Namespace, settings: Settings, label: str) -> int:
    """``ferro run --processes N``: binds once, then N workers share the socket."""
    from ferro.supervisor import BindError, supervise

    worker_argv = ["_worker", args.target]
    for option, setting, _ in _SERVER_OPTIONS:
        value = getattr(args, setting)
        if value is not None and setting != "processes":
            worker_argv += [option, str(value)]
    if args.debug is not None:
        worker_argv.append("--debug" if args.debug else "--no-debug")
    banner = (
        f"Ferro {__version__} serving {args.target} on {{url}} "
        f"({label}, {settings.processes} processes, {settings.workers} workers each)"
    )
    from ferro import _ferro

    if settings.tls_cert:
        try:
            _ferro.check_tls(settings.tls_cert, settings.tls_key)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from None
    # Rate limits and metrics shared by the workers (Feature 015); private to this user.
    shared_dir = tempfile.mkdtemp(prefix="ferro-")
    try:
        _ferro.create_shared_state(os.path.join(shared_dir, "rates.shm"))
        worker_argv += ["--shared-dir", shared_dir]
        return supervise(
            settings.host,
            settings.port,
            settings.processes,
            worker_argv,
            banner,
            settings.shutdown_timeout + settings.shutdown_drain,
            scheme=settings.scheme,
        )
    except BindError as exc:
        _error(str(exc))
        return EXIT_BIND
    finally:
        shutil.rmtree(shared_dir, ignore_errors=True)


def _worker(args: argparse.Namespace) -> int:
    """One worker of ``ferro run --processes N`` (started by the supervisor, not by users)."""
    from ferro.server import serve
    from ferro.supervisor import adopt_listener, watch_supervisor

    listener = adopt_listener()
    app = load_app(args.target)
    cli = {**_cli_settings(args), "processes": 1}
    settings = resolve(cli=cli, app=app._settings, mode="run")
    watch_supervisor(settings.shutdown_timeout + settings.shutdown_drain)
    return serve(app, settings, listener=listener, shared_dir=args.shared_dir)


def _routes(args: argparse.Namespace) -> int:
    app = load_app(args.target)
    rows = []
    for route in app.route_records:
        if route.websocket:
            rows.append(("WEBSOCKET", route.path, route.name))
            continue
        methods = set(route.methods)
        if "GET" in methods:
            methods.add("HEAD")
        ordered = ", ".join(m for m in _CANONICAL_METHODS if m in methods)
        rows.append((ordered, route.path, route.name))
    order = (*_CANONICAL_METHODS, "WEBSOCKET")
    rows.sort(key=lambda row: (row[1], order.index(row[0].split(", ")[0])))
    header = ("METHODS", "PATH", "HANDLER")
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(2)]
    for row in [header, *rows]:
        print(f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]}".rstrip())
    return EXIT_OK


def _uses_migrations(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("[0-9][0-9][0-9][0-9]_*.py"))


def _database_url(args: argparse.Namespace, app: App) -> str:
    settings = resolve(cli={"database_url": args.database_url}, app=app._settings, mode="run")
    if settings.database_url is None:
        raise ConfigurationError(
            "no database URL; pass --database-url, set FERRO_DATABASE_URL "
            "or use App(database_url=...)"
        )
    return settings.database_url


def _with_database(url: str, work: Any) -> Any:
    from ferro import db

    async def run() -> Any:
        await db.connect(url)
        try:
            return await work()
        finally:
            await db.disconnect()

    return asyncio.run(run())


def _makemigrations(args: argparse.Namespace) -> int:
    from ferro.migrations import commands

    load_app(args.target)
    directory = Path(args.migrations_dir)
    result = commands.make_migrations(directory, name=args.name, check=args.check, empty=args.empty)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.check:
        if not result.operations:
            print("No changes detected")
            return EXIT_OK
        print("Changes detected:")
        for operation in result.operations:
            print(f"  - {operation.describe()}")
        return EXIT_FAILURE
    if result.path is None:
        print("No changes detected")
        return EXIT_OK
    shown = result.path.relative_to(Path.cwd()) if result.path.is_absolute() else result.path
    print(f"Created {shown.as_posix()}")
    for operation in result.operations:
        print(f"  - {operation.describe()}")
    return EXIT_OK


def _progress(verb: str) -> tuple[Any, Any]:
    def before(name: str) -> None:
        print(f"{verb} {name}...", end="", flush=True)

    def after(name: str) -> None:
        print(" OK", flush=True)

    return before, after


def _migrate(args: argparse.Namespace) -> int:
    from ferro.migrations import commands

    app = load_app(args.target)
    url = _database_url(args, app)
    before, after = _progress("Applying")
    try:
        applied = _with_database(
            url, lambda: commands.migrate(args.migrations_dir, before=before, after=after)
        )
    except (DatabaseError, MigrationError):
        print(" FAILED", flush=True)
        raise
    if not applied:
        print("No migrations to apply")
    if "ferro.auth" in sys.modules:
        from ferro.auth import create_permissions

        created = _with_database(url, create_permissions)
        if created:
            print(f"Created {created} permissions")
    return EXIT_OK


def _interactive() -> bool:
    """Whether stdin is a real terminal (on Windows the NUL device also claims to be a tty)."""
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        mode = ctypes.c_ulong()
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def _read_password(prompt: bool) -> str:
    password = os.environ.get("FERRO_SUPERUSER_PASSWORD")
    if password:
        return password
    if not prompt or not _interactive():
        raise ConfigurationError(
            "no password: set FERRO_SUPERUSER_PASSWORD or run createsuperuser in a terminal"
        )
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Password (again): "):
        raise ConfigurationError("the passwords do not match")
    return first


def _createsuperuser(args: argparse.Namespace) -> int:
    app = load_app(args.target)
    from ferro.auth import PasswordError, User

    url = _database_url(args, app)
    password = _read_password(prompt=not args.no_input)

    async def create() -> Any:
        if await User.objects.filter(username=args.username.strip()).exists():
            raise ConfigurationError(f"user {args.username!r} already exists")
        return await User.objects.create_superuser(args.username, password, email=args.email)

    try:
        user = _with_database(url, create)
    except PasswordError as error:
        _error(error.message)
        return EXIT_FAILURE
    print(f"Superuser {user.username!r} created")
    return EXIT_OK


def _rollback(args: argparse.Namespace) -> int:
    from ferro.migrations import commands

    app = load_app(args.target)
    url = _database_url(args, app)
    before, after = _progress("Reverting")
    try:
        reverted = _with_database(
            url,
            lambda: commands.rollback(
                args.migrations_dir, steps=args.steps, to=args.to, before=before, after=after
            ),
        )
    except (DatabaseError, MigrationError):
        print(" FAILED", flush=True)
        raise
    if not reverted:
        print("Nothing to roll back")
    return EXIT_OK


def _migration_status(args: argparse.Namespace) -> int:
    from ferro.migrations import commands

    app = load_app(args.target)
    entries = _with_database(_database_url(args, app), lambda: commands.status(args.migrations_dir))
    for name, applied in entries:
        print(f"[{'X' if applied else ' '}] {name}")
    return EXIT_OK


def _warn_unapplied(url: str) -> None:
    from ferro.migrations import commands

    directory = Path("migrations")
    if not _uses_migrations(directory):
        return
    try:
        entries = _with_database(url, lambda: commands.status(directory))
    except Exception as exc:
        print(f"warning: could not check migrations: {exc}", file=sys.stderr, flush=True)
        return
    pending = [name for name, applied in entries if not applied]
    if pending:
        print(
            f"warning: {len(pending)} unapplied migration(s): {', '.join(pending)}; "
            "run `ferro migrate`",
            file=sys.stderr,
            flush=True,
        )


def _create_tables(args: argparse.Namespace) -> int:
    from ferro import db

    if _uses_migrations(Path("migrations")):
        raise ConfigurationError(
            "this project uses migrations; run `ferro migrate` instead of `ferro db create-tables`"
        )
    app = load_app(args.target)
    settings = resolve(cli={"database_url": args.database_url}, app=app._settings, mode="run")
    if settings.database_url is None:
        raise ConfigurationError(
            "no database URL; pass --database-url, set FERRO_DATABASE_URL "
            "or use App(database_url=...)"
        )

    async def run() -> tuple[list[str], list[str]]:
        await db.connect(settings.database_url)
        try:
            return await db.create_tables()
        finally:
            await db.disconnect()

    created, existing = asyncio.run(run())
    for table in created:
        print(f"created  {table}")
    for table in existing:
        print(f"exists   {table}")
    return EXIT_OK


def _sequence_reset(args: argparse.Namespace) -> int:
    from ferro import db

    app = load_app(args.target)
    settings = resolve(cli={"database_url": args.database_url}, app=app._settings, mode="run")
    if settings.database_url is None:
        raise ConfigurationError(
            "no database URL; pass --database-url, set FERRO_DATABASE_URL "
            "or use App(database_url=...)"
        )

    async def run() -> tuple[list[str], list[str]]:
        await db.connect(settings.database_url)
        try:
            return await db.reset_sequences()
        finally:
            await db.disconnect()

    checked, moved = asyncio.run(run())
    if not checked:
        print("nothing to do (no PostgreSQL sequences)")
    for table in checked:
        print(f"{'moved' if table in moved else 'ok':<8} {table}")
    return EXIT_OK


def _new(args: argparse.Namespace) -> int:
    from ferro.project import create_project

    directory = create_project(args.name, force=args.force)
    print(f"Created project {directory.resolve().name!r} in {directory}")
    print("Next steps:")
    if args.name != ".":
        print(f"  cd {args.name}")
    print("  ferro makemigrations && ferro migrate")
    print("  ferro dev")
    return EXIT_OK


def _shell(args: argparse.Namespace) -> int:
    from ferro import db
    from ferro.shell import namespace, run_interactive, run_source

    app = load_app(args.target)
    settings = resolve(cli={"database_url": args.database_url}, app=app._settings, mode="run")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if settings.database_url:
            loop.run_until_complete(db.connect(settings.database_url))
        scope = namespace(app)
        if args.code is not None:
            return run_source(args.code, scope, loop)
        if not _interactive():
            return run_source(sys.stdin.read(), scope, loop)
        return run_interactive(scope, loop)
    finally:
        if db.is_connected():
            loop.run_until_complete(db.disconnect())
        loop.close()


def _db_shell(args: argparse.Namespace) -> int:
    import shutil
    import subprocess
    from urllib.parse import urlsplit, urlunsplit

    app = load_app(args.target)
    url = _database_url(args, app)
    environment = dict(os.environ)
    if url.startswith("sqlite:"):
        path = url.removeprefix("sqlite:///")
        if path == ":memory:":
            raise ConfigurationError("an in-memory SQLite database has no client to open")
        program, arguments = "sqlite3", [path]
    else:
        parts = urlsplit(url)
        if parts.password:
            environment["PGPASSWORD"] = parts.password
        host = parts.hostname or ""
        netloc = f"{parts.username}@{host}" if parts.username else host
        if parts.port:
            netloc += f":{parts.port}"
        program = "psql"
        arguments = [urlunsplit(("postgresql", netloc, parts.path, parts.query, ""))]
    client = shutil.which(program)
    if client is None:
        raise ConfigurationError(
            f"the {program} client is not installed or not on PATH (needed by ferro db shell)"
        )
    return subprocess.run([client, *arguments], env=environment).returncode


def _worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ferro _worker")
    _add_server_options(parser)
    parser.add_argument("--shared-dir", default=None)
    parser.set_defaults(command="_worker")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["_worker"]:
        args = _worker_parser().parse_args(arguments[1:])
    else:
        args = _parser().parse_args(arguments)
    try:
        from ferro.dotenv import load as load_dotenv

        load_dotenv(Path(".env"))
        if args.command == "_worker":
            return _worker(args)
        if args.command == "routes":
            return _routes(args)
        if args.command == "new":
            return _new(args)
        if args.command == "shell":
            return _shell(args)
        if args.command == "db" and args.db_command == "shell":
            return _db_shell(args)
        if args.command == "db" and args.db_command == "sequence-reset":
            return _sequence_reset(args)
        if args.command == "db":
            return _create_tables(args)
        if args.command == "makemigrations":
            return _makemigrations(args)
        if args.command == "migrate":
            return _migrate(args)
        if args.command == "rollback":
            return _rollback(args)
        if args.command == "migration":
            return _migration_status(args)
        if args.command == "createsuperuser":
            return _createsuperuser(args)
        if args.command == "dev" and args.reload:
            from ferro.reloader import run_with_reload

            _single_process(args)
            check_target(args.target)
            watch = [Path(d).resolve() for d in args.reload_dir] or [Path.cwd()]
            return run_with_reload(_child_argv(args), watch)
        return _serve(args, args.command)
    except ConfigurationError as exc:
        _error(str(exc))
        return EXIT_CONFIG
    except _UserImportError as exc:
        print(str(exc), file=sys.stderr, end="", flush=True)
        _error(f"importing '{args.target}' failed (see traceback above)")
        return EXIT_CONFIG
    except (DatabaseError, MigrationError) as exc:
        _error(str(exc))
        return EXIT_FAILURE
