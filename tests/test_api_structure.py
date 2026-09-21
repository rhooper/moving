"""Structural invariants of the HTTP layer."""

import inspect

from starlette.routing import WebSocketRoute

from movingbox.api.app import create_app, get_conn


def all_routes(router):
    """Every route, flattened.

    `include_router` does not flatten into `app.routes` in this FastAPI
    version: each included router is one `_IncludedRouter` wrapper whose real
    routes hang off `original_router`. The wrapper's own `routes` attribute is a
    *string*, so walking it silently inspects nothing.
    """
    children = getattr(router, "routes", None)
    if not isinstance(children, (list, tuple)):
        children = []

    for route in children:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from all_routes(inner)
        elif isinstance(getattr(route, "routes", None), (list, tuple)):
            yield from all_routes(route)
        else:
            yield route


def uses(dependant, target) -> bool:
    """Whether `target` appears anywhere in a route's dependency tree."""
    for sub in getattr(dependant, "dependencies", []):
        if sub.call is target or uses(sub, target):
            return True
    return False


def test_the_route_walk_actually_finds_routes(config):
    # If flattening breaks, the invariants below pass vacuously.
    paths = {getattr(r, "path", None) for r in all_routes(create_app(config))}

    assert "/api/boxes/{code}/photos" in paths
    assert "/api/boxes/{code}" in paths


def test_no_route_using_the_database_is_async(config):
    """A sync dependency plus an async endpoint means a cross-thread sqlite handle.

    get_conn runs in FastAPI's threadpool while an `async def` endpoint runs on
    the event loop, and sqlite3 refuses a connection used across threads.
    """
    offenders = []
    for route in all_routes(create_app(config)):
        endpoint = getattr(route, "endpoint", None)
        dependant = getattr(route, "dependant", None)
        if endpoint is None or dependant is None:
            continue
        if uses(dependant, get_conn) and inspect.iscoroutinefunction(endpoint):
            offenders.append(f"{route.path} ({endpoint.__name__})")

    assert offenders == [], (
        "these routes take a database connection but are async: "
        + ", ".join(offenders)
        + " -- make them sync `def`"
    )


def test_no_websocket_takes_a_database_connection(config):
    """The same rule, from the other end.

    A websocket endpoint must be `async def`, so it has to need no database at
    all. That is why the change channel carries only an event kind and a box
    code, and clients re-fetch.
    """
    sockets = [r for r in all_routes(create_app(config)) if isinstance(r, WebSocketRoute)]

    assert [r.path for r in sockets] == ["/api/events"], "the change socket moved or vanished"
    offenders = [r.path for r in sockets if uses(r.dependant, get_conn)]
    assert offenders == [], (
        "these websockets take a database connection: "
        + ", ".join(offenders)
        + " -- an async endpoint plus a threadpool sqlite handle is a crash"
    )
