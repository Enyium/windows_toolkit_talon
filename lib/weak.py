import weakref
from collections.abc import Callable
from typing import ParamSpec, TypeAlias, TypeVar
from weakref import ReferenceType, WeakMethod

P = ParamSpec("P")
R = TypeVar("R")
C = TypeVar("C", bound=Callable[..., object])

WeakCallback: TypeAlias = ReferenceType[C] | WeakMethod[C]

def to_weak_callback(func: Callable[P, R]) -> WeakCallback[Callable[P, R]]:
    try:
        return WeakMethod(func)
    except TypeError:
        return weakref.ref(func)

def call_weak(weak_func: WeakCallback[Callable[P, R]] | None, *args: P.args, **kwargs: P.kwargs) -> R | None:
    """Calls the function, if available, or does nothing."""

    if weak_func is not None:
        func = weak_func()
        if func is not None:
            return func(*args, **kwargs)
