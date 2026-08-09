# Python Type Hints — Optional, Union, TypeVar, Generic

Python's type system, formalized by PEP 484 and progressively extended in subsequent PEPs, supports gradual static typing without changing runtime behavior. The `typing` module exposes the building blocks. Modern code uses the syntactic shortcuts introduced in Python 3.9+ (`list[int]`) and 3.10+ (`int | None`) where possible, but the underlying concepts remain.

## Optional vs Union

`Optional[X]` is equivalent to `Union[X, None]`. Use `Optional` when a value can legitimately be `None` — typically as a sentinel for "no value" or "lookup failed". Use `Union[X, Y]` when a value can be one of several types where `None` is not the disambiguator. In Python 3.10+, both are usually written with the pipe syntax: `X | None` for optional, `int | str` for unions.

A common mistake is confusing `Optional[X]` with "optional argument". A function parameter with a default value of `None` should be typed as `Optional[X]` (or `X | None`), not just `X`. The default value doesn't change the type.

## TypeVar and generics

`TypeVar` lets you express that the type of one parameter or return value depends on another. The classic example:

```python
from typing import TypeVar
T = TypeVar("T")

def first(items: list[T]) -> T | None:
    return items[0] if items else None
```

Calling `first([1, 2, 3])` returns `int | None`; calling `first(["a", "b"])` returns `str | None`. The type checker tracks the substitution.

Bounded type variables constrain the generic: `T = TypeVar("T", bound=Comparable)` restricts T to types that satisfy a Comparable protocol.

## Generic classes

A generic class inherits from `Generic[T]`:

```python
from typing import Generic, TypeVar

T = TypeVar("T")

class Stack(Generic[T]):
    def push(self, item: T) -> None: ...
    def pop(self) -> T: ...
```

Users instantiate with a concrete type: `Stack[int]()`. The type checker enforces consistent use of `T` within the class.

## Protocols (structural typing)

`Protocol` (PEP 544) provides structural typing — duck typing checked statically. A type satisfies a Protocol when it has the right methods, regardless of whether it explicitly inherits. Useful for typing third-party objects you can't subclass.

```python
from typing import Protocol

class Comparable(Protocol):
    def __lt__(self, other: object) -> bool: ...
```

## When to use what

For function signatures, annotate the public contract — argument types and return type. For internal helpers, annotate when the type isn't obvious. For dataclasses and Pydantic models, the field annotations drive runtime behavior, so they're mandatory.

Type checkers like mypy, pyright, or pyre catch the bugs that runtime testing misses — wrong argument types, missing None checks, incompatible return types. Adopt them gradually; add types to new code first, then backfill hot paths.
