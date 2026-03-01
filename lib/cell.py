from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(slots=True)
class Cell(Generic[T]):
    """A mutable storage location to a single value.

    Enables you to share data with an immutable type where this would otherwise not be possible.
    """

    value: T
