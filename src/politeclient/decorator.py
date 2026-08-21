"""The ``@polite`` decorator.

Sometimes you don't want to thread a client object through your code — you just
want a function that "does the polite thing" every time it runs. ``@polite``
builds a shared :class:`PoliteClient` once and injects it as the first argument
of the decorated function:

    >>> @polite(rate_limit=RateLimit(rate=5), retry=RetryPolicy(max_retries=4))
    ... def fetch_user(client, user_id):
    ...     return client.get(f"https://api.example.com/users/{user_id}").json()
    ...
    >>> fetch_user(42)              # the client is injected for you
    >>> fetch_user.client.close()   # the underlying client is reachable
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from .client import PoliteClient

F = TypeVar("F", bound=Callable[..., Any])


def polite(**client_kwargs: Any) -> Callable[[F], F]:
    """Wrap a function so it receives a shared :class:`PoliteClient` as arg 0.

    All keyword arguments are forwarded to :class:`PoliteClient`. The live client
    is attached to the wrapper as ``.client`` so you can inspect or close it.
    """

    def decorator(func: F) -> F:
        client = PoliteClient(**client_kwargs)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(client, *args, **kwargs)

        wrapper.client = client  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
