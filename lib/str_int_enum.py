from enum import IntEnum
from typing import Self

class StrCarryingOneBasedIntEnum(IntEnum):
    """An enum with the following features:
    - You assign strings to members, but the class auto-assigns integers with step size 1.
    - The lowest integer value is 1, allowing easier interop with WinAPI functions like `GetPropW()`, whose return value 0 means that the property doesn't exist.
    - While the instances behave like `int`s, stringifying them yields the strings you assigned.
    """

    def __new__(cls, string: str) -> Self:
        value = len(cls) + 1

        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.__string = string
        return obj

    @classmethod
    def zero_based_len(cls) -> int:
        return 1 + len(cls)

    def __str__(self) -> str:
        return self.__string
