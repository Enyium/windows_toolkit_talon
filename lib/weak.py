 #TODO: In jeder Datei einsetzen, die `WeakMethod` verwendet. Dort u.a. statt `WeakMethod` als Parametertyp normale Funktionen (gebunden oder nicht) akzeptieren und sie vorm Sichern durch `weak_callable()` schicken, damit der Instanziierer der Klasse das nicht tun muss.

from typing import Callable, Optional, ParamSpec, TypeAlias, TypeVar, Union
import weakref
from weakref import ReferenceType, WeakMethod

P = ParamSpec("P")
R = TypeVar("R")

WeakCallable: TypeAlias = Union[
    ReferenceType[Callable[P, R]],
    WeakMethod[Callable[P, R]],
]

def weak_callable(func: Callable[P, R]) -> WeakCallable[P, R]:
    try:
        return WeakMethod(func)
    except TypeError:
        return weakref.ref(func)

def call_weak(weak_func: Optional[WeakCallable[P, R]], *args: P.args, **kwargs: P.kwargs) -> Optional[R]:
    """Calls the function, if available, or does nothing."""

    if weak_func is not None:
        func = weak_func()
        if func is not None:
            return func(*args, **kwargs)
