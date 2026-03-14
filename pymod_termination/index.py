#TODO: AFTER TALON V0.4: Try filename `__init__.py` again.
#i Because of a bug in Talon v0.4.0, this file can't yet be named `__init__.py`.

#TODO: WITH TALON AUTHOR: Please also make modules be unloaded on file removals (`[-]` in log) instead of just when the exact same path is later possibly involved in a file addition (`[+]` in log). On changes (`[~]` in log), it already works. (See also my private notes.) WHEN SOLVED: Change info in code (search for "bug").
#TODO: WITH TALON AUTHOR: Can memory leak be solved in Talon? These points stay relevant, though: callbacks, teardown deferral, explicit `gc.collect()`. Simple quick teardown would worsen use cases with own threads.
"""
This module allows you to register callbacks that will be run after Talon script reloads to give you an opportunity to clean up old objects. It also solves the memory leak that goes along with script reloads, which means finalizers for old module objects can't be forever pending anymore.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import auto, Enum
import gc
import sys
import textwrap
from threading import Lock, RLock
import traceback
from types import ModuleType, NoneType, TracebackType
from typing import Any, Callable, Literal, Optional, ParamSpec, Self, TypeAlias, TypeVar
import weakref

from ..lib.weak import WeakCallback, to_weak_callback

T = TypeVar("T")
P = ParamSpec("P")

#. Debug settings.
_IS_DEBUGGING = False
_ATTRIBUTE_TO_SPARE = "debug attribute {154fa138-b997-4f3b-906f-dd81d91cf617}"

_ACCEPTABLE_ATTRIBUTES = frozenset(("__all__", "__builtins__", "__cached__", "__doc__", "__file__", "__loader__", "__name__", "__package__", "__path__", "__spec__"))
"""Attributes recommended by AI to avoid causing problems. Could change with future versions of Python."""
_ACCEPTABLE_TYPES = frozenset((NoneType, bool, int, float, complex, range, bytes, str))
"""For more possible types, see also <https://docs.python.org/3/library/types.html#standard-interpreter-types>."""

_hooks_dict_lock = Lock()
_hooks_by_module_keys: dict[_ModuleKey, TerminationHook] = {}

def get_pymod_termination_hook(module_name: Optional[str] = None) -> TerminationHook:
    """Returns an object that you can use

    - to register callbacks for when the Python module object is garbage-collected;
    - to register callbacks called on teardown of the module globals; and
    - to defer teardown, so that you don't lose access to globals while a function is running, specifically in a separate thread.

    A module name of `None` means the module you called this function from. Calling the function repeatedly for the same module - with or without arguments - returns the same object.

    Note that because of a bug in Talon (at least in v0.4.0), termination can only be recognized when the old and the new script have the same file path; so, not with script removals (marked with `[-]` in Talon log), which includes renaming and moving.
    """

    if module_name is None:
        #TODO: WHEN HAVING PYTHON V3.12 OR LATER: Use `sys._getframemodulename(1)` instead.
        module_name = sys._getframe(1).f_globals["__name__"]

    try:
        module_object = sys.modules[module_name]
    except KeyError:
        raise ValueError(f"Couldn't find module with name `{module_name}`.")
    module_key = _ModuleKey(module_name, id(module_object.__dict__))

    with _hooks_dict_lock:
        hook = _hooks_by_module_keys.get(module_key)
        if not hook:
            _hooks_by_module_keys[module_key] = hook = TerminationHook(module_object, module_key)

    return hook


@dataclass(frozen=True)
class _ModuleKey:
    name: str
    """The module name."""
    dict_id: int
    """The `__dict__`'s instead of the module object's ID, because IDs may be reused after garbage collection and only the `__dict__` survives until teardown is complete."""

    def __str__(self):
        return f"module `{self.name}` with `__dict__` at 0x{self.dict_id:X}"


class TerminationHook:
    __WeakCallbackQueue: TypeAlias = deque[WeakCallback[Callable[[], None]]]

    class _Command(Enum):
        FINALIZE_MODULE_OBJECT = auto()
        INCREMENT_TEARDOWN_DEFERRERS = auto()
        DECREMENT_TEARDOWN_DEFERRERS = auto()

    def __init__(self, module_object: ModuleType, module_key: _ModuleKey) -> None:
        """Private constructor; call `get_pymod_termination_hook()`."""

        self.__lock = RLock()
        self.__module_key = module_key
        self.__module_dict: Optional[dict[str, Any]] = module_object.__dict__
        self.__module_object_finalized = False
        self.__weak_on_finalize_callbacks: TerminationHook.__WeakCallbackQueue = deque()
        self.__torn_down = False
        self.__num_teardown_deferrers = 0
        self.__weak_on_teardown_callbacks: TerminationHook.__WeakCallbackQueue = deque()

        weakref.finalize(module_object, self._do, self._Command.FINALIZE_MODULE_OBJECT)

        if _IS_DEBUGGING:
            obj = frozenset()  # Something weakly referencable.
            self.__module_dict[_ATTRIBUTE_TO_SPARE] = obj
            weakref.finalize(obj, print, f"Finalizing `__dict__` of {module_key}.")
            #i The dict itself isn't weakly referenceable and can thus not be used directly.

    def on_module_finalize(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Registers the given function to be called when the module object previously in `sys.modules` is garbage-collected (the first thing that happens with the old module). Can also be used as a decorator for static methods.

        Since the function runs inside a `weakref.finalize()` callback, it must adhere to the same restrictions regarding its behavior as a `weakref.finalize()` callback. It may also run in any thread that happens to perform garbage collection.

        Multiple callbacks are called in the order of their registration.
        """

        with self.__lock:
            if self.__module_object_finalized:
                raise RuntimeError("Module object already finalized.")

            self.__add_callback(self.__weak_on_finalize_callbacks, callback)

        return callback

    def on_globals_teardown(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Registers the given function to be called just before teardown of the module namespace, which this class performs. Can also be used as a decorator for static methods.

        Teardown means setting certain module globals in the remaining module `__dict__` to `None` to break Python-inherent reference cycles, thus making the globals be garbage-collected, if possible. Since functions (incl. instance methods) hold the module `__dict__` in their `__globals__` attribute, this also releases the module `__dict__`, if no other reference cycles were introduced. Globals from a small set of double-dunder names or with primitive data types will be left as they are. Garbage collection is triggered explicitly, leading to any of your `weakref.finalize()` callbacks for old objects being called, if possible. In these callbacks, many of the module globals will be `None`, as previously described, contrary to what the docs of `weakref.finalize()` convey.

        The function runs in the `weakref.finalize()` callback mentioned in the docs for `on_module_finalize()`, if there are no deferrers at that point, or otherwise in the thread that exits the last deferrer context manager.

        Multiple callbacks are called in the order of their registration.
        """

        with self.__lock:
            if self.__torn_down:
                raise RuntimeError("Module already torn down.")

            self.__add_callback(self.__weak_on_teardown_callbacks, callback)

        return callback

    @staticmethod
    def __add_callback(weak_callbacks: __WeakCallbackQueue, callback: Callable[[], None]) -> None:
        """You must lock the queue."""

        if len(weak_callbacks) > 100:
            # Remove dead entries.
            write_index = 0
            for weak_callback in weak_callbacks:
                if weak_callback() is not None:
                    weak_callbacks[write_index] = weak_callback
                    write_index += 1

            while len(weak_callbacks) > write_index:
                weak_callbacks.pop()

        weak_callbacks.append(to_weak_callback(callback))

    @property
    def globals_teardown_deferrer(self) -> TeardownDeferrer:
        """Returns a context manager that defers teardown, so module globals aren't cleared (in a thread-unsafe manner) while the code is running, e.g., in a separate thread that your code maintains.

        - Make sure to start the context manager's `with` block while teardown couldn't already have happened, e.g., by synchronizing it with the code in the regular thread.
        - The deferrer context must only be exited at a point after which no module globals will be accessed anymore.
        """

        return TeardownDeferrer(self)

    def _do(self, command: _Command) -> None:
        is_finalizing = False
        must_tear_down = False

        with self.__lock:
            if self.__torn_down:
                raise RuntimeError("Module already torn down.")

            match command:
                case self._Command.FINALIZE_MODULE_OBJECT:
                    if self.__module_object_finalized:
                        raise RuntimeError("Module object already finalized.")

                    if _IS_DEBUGGING:
                        print(f"Finalizing {self.__module_key}.")

                    self.__module_object_finalized = is_finalizing = True
                    if self.__num_teardown_deferrers <= 0:
                        self.__torn_down = must_tear_down = True

                case self._Command.INCREMENT_TEARDOWN_DEFERRERS:
                    self.__num_teardown_deferrers += 1

                    if _IS_DEBUGGING:
                        print(f"Incremented teardown deferrers for {self.__module_key}. New count: {self.__num_teardown_deferrers}.")

                case self._Command.DECREMENT_TEARDOWN_DEFERRERS:
                    self.__num_teardown_deferrers -= 1
                    assert self.__num_teardown_deferrers >= 0

                    if _IS_DEBUGGING:
                        print(f"Decremented teardown deferrers for {self.__module_key}. New count: {self.__num_teardown_deferrers}.")

                    if self.__module_object_finalized and self.__num_teardown_deferrers <= 0:
                        self.__torn_down = must_tear_down = True

        # Run event callbacks.
        for finalize_xor_teardown, callbacks in (
            (True, self.__weak_on_finalize_callbacks if is_finalizing else None),
            (False, self.__weak_on_teardown_callbacks if must_tear_down else None),
            #i The respective flags denying access to further mutations are already set. So, we can access the callback queues without lock.
        ):
            while callbacks:
                weak_callback = callbacks.popleft()
                callback = weak_callback()
                if callback:
                    try:
                        callback()
                    except BaseException:
                        print(
                            f"ERROR: Unhandled exception in {'finalize' if finalize_xor_teardown else 'teardown'} callback `{callback}` for {self.__module_key}:\n"
                            + textwrap.indent(traceback.format_exc().rstrip(), "  ")
                        )

        #
        if must_tear_down:
            self.__tear_down()

    def __tear_down(self) -> None:
        """Tears the module `__dict__` down, i.e., the module's namespace or globals. Doesn't use the instance's lock and must only be called once."""

        if _IS_DEBUGGING:
            print(f"Start of teardown of {self.__module_key}.")

        with _hooks_dict_lock:
            try:
                del _hooks_by_module_keys[self.__module_key]
            except KeyError:
                pass

        # Clear module globals that may hold the module dict at any depth level.
        # (e.g., instance methods in their `__globals__` attribute)
        attributes = list(self.__module_dict.keys())
        #i Copying avoids `RuntimeError` "dictionary changed size during iteration" when another thread edits the dictionary. Still, it should be arranged so that concurrent edits don't happen, which deferrers encourage.

        if _IS_DEBUGGING:
            cleared_attributes = []

        objects_to_delete = []
        #i For more controlled removal, specifically in relation to debug output.

        for attribute in attributes:
            value = self.__module_dict.get(attribute)
            acceptable = (
                attribute in _ACCEPTABLE_ATTRIBUTES
                or type(value) in _ACCEPTABLE_TYPES
                or (_IS_DEBUGGING and attribute == _ATTRIBUTE_TO_SPARE)
            )
            if not acceptable:
                if _IS_DEBUGGING:
                    cleared_attributes.append(attribute)

                objects_to_delete.append(value)

                self.__module_dict[attribute] = None
                #i - Likely includes this class instance.
                #i - Python itself also sets the attributes to `None` on interpreter shutdown.
                #i - If useful, the objects could also be replaced with weak references to themselves.

        if _IS_DEBUGGING:
            print(f"Clearing following globals of {self.__module_key}: {cleared_attributes}")

        del value
        del objects_to_delete
        #i The objects being deleted may reentrantly cause finalization and teardown of imported modules in the context of Talon reload chains.
        self.__module_dict = None

        # Ensure old objects are finalized immediately, if possible.
        gc.collect()

        #
        if _IS_DEBUGGING:
            print(f"End of teardown of {self.__module_key}.")


class TeardownDeferrer:
    def __init__(self, hook: TerminationHook) -> None:
        """Private constructor."""

        self.__hook = hook
        self.__entered = False

    def __enter__(self) -> Self:
        if not self.__hook:
            raise RuntimeError(f"`{TeardownDeferrer.__name__}` already consumed.")

        self.__entered = True
        self.__hook._do(TerminationHook._Command.INCREMENT_TEARDOWN_DEFERRERS)
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[Literal[True]]:
        if not self.__entered:
            raise RuntimeError("`__exit__()` without previous `__enter__()`.")
        if not self.__hook:
            raise RuntimeError(f"`{TeardownDeferrer.__name__}` already consumed.")

        self.__hook._do(TerminationHook._Command.DECREMENT_TEARDOWN_DEFERRERS)
        self.__hook = None  # Context manager consumed.
