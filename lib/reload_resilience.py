 #TODO: WITH TALON AUTHOR: On script reloads, will the old module always survive until the new module has been run? If yes, simple `WeakValueDictionary`s suffice for reload resilience logic. On reload chains, the old batch of modules should probably be garbage-collected after the whole new batch ran.
"""
A stepping stone to transfer objects between Talon script reloads. Reloading this script will result in loss of its held data.
"""

from threading import Lock
from typing import Any, Optional
from uuid import UUID
from weakref import WeakValueDictionary

_lock = Lock()
_weak_dict: WeakValueDictionary[UUID, Any] = WeakValueDictionary()
_arg_missing_sentinel = object()

def set_value(uuid4: UUID, value: Any) -> Any:
    """Sets the value and returns it. The value type must be capable of being referenced weakly, or a `TypeError` will be raised."""

    with _lock:
        _weak_dict[uuid4] = value  # May raise `TypeError`.

    return value

def pop_value(uuid4: UUID, default: Any = _arg_missing_sentinel) -> Optional[Any]:
    """Returns the set value. If it was already garbage-collected, returns `default` or raises a `KeyError`."""

    try:
        with _lock:
            return _weak_dict.pop(uuid4)
    except KeyError:
        if default is _arg_missing_sentinel:
            raise
        else:
            return default
