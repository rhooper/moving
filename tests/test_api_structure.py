"""Structural invariants of the HTTP layer."""

import inspect

from movingbox.api.app import create_app, get_conn


def all_routes(router):
    """Every route, flattened.

    `include_router` does not flatten into `app.routes` in this FastAPI
    version: each included router appears as one `_IncludedRouter` wrapper, and
    the real routes hang off its `original_router`. Note that the wrapper also
    has a `routes` attribute which is a *string* -- walking that yields its
    characters and silently inspects nothing, which is how the first version of
    this helper passed while testing almost no routes.
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
    # Guards the guard: if flattening breaks, the invariant below would pass
    # vacuously and stop protecting anything.
    paths = {getattr(r, "path", None) for r in all_routes(create_app(config))}

    assert "/api/boxes/{code}/photos" in paths
    assert "/api/boxes/{code}" in paths


def test_no_route_using_the_database_is_async(config):
    """A sync dependency plus an async endpoint means a cross-thread sqlite handle.

    get_conn is a sync generator dependency, so FastAPI runs it in a
    threadpool. An `async def` endpoint runs on the event loop instead, so the
    connection would be created in one thread and used in another -- which
    sqlite3 rejects outright. Photo upload hit this for real.
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
