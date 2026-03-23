"""Internal generic stream and group helpers.

These base classes collect the storage and collection mechanics shared by
domain-specific stream objects such as cashflows and generation. They are
intentionally internal: public modules should continue to provide the
user-facing API, domain language, and full end-user documentation.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Collection, Iterable, Iterator, Protocol, Self, overload

from dcaf.types import Period, SupportsLessThan
from dcaf.utils import period_start


class _StreamProtocol[EntryT](Protocol):
    """Minimal protocol required by :class:`BaseGroup`."""

    entries: list[EntryT]

    def count(self) -> int: ...

    def sum(self) -> float: ...

    def _new(self, entries: Iterable[EntryT]) -> Self: ...


@dataclass
class BaseStream[EntryT]:
    """Reusable collection behavior for entry-based stream classes.

    Subclasses are expected to store their records in ``entries`` and define
    the domain-specific pieces that cannot be inferred generically, such as
    how to read an entry amount or which named attributes are valid sort keys.
    """

    entries: list[EntryT] = field(default_factory=list)

    @classmethod
    def _validate_stream_type(cls, stream: "BaseStream[object]") -> None:
        """Reject combining streams from different concrete domains."""
        if type(stream) is not cls:
            raise TypeError(
                f"Cannot combine {cls.__name__} with {type(stream).__name__}"
            )

    def _new(self, entries: Iterable[EntryT]) -> Self:
        """Construct a new instance of the current concrete stream type."""
        return type(self)(list(entries))

    def _amount(self, entry: EntryT) -> float:
        """Return the numeric amount used by :meth:`sum`."""
        raise NotImplementedError

    def _resolve_sort_key(self, attr: str) -> Callable[[EntryT], SupportsLessThan]:
        """Return the key function for a named sort attribute."""
        return lambda entry: getattr(entry, attr)

    @classmethod
    def from_streams(
        cls, *iterables: "BaseStream[EntryT] | EntryT | Iterable[EntryT]"
    ) -> Self:
        """Combine stream objects, single entries, and iterables into one stream."""
        all_entries: list[EntryT] = []

        for item in iterables:
            if isinstance(item, BaseStream):
                cls._validate_stream_type(item)
                all_entries.extend(item.entries)
            else:
                try:
                    iterator = iter(item)
                except TypeError:
                    all_entries.append(item)
                else:
                    all_entries.extend(iterator)

        return cls(all_entries)

    @overload
    def __getitem__(self, index: int) -> EntryT: ...

    @overload
    def __getitem__(self, index: slice) -> Self: ...

    def __getitem__(self, index: int | slice) -> EntryT | Self:
        """Return a single entry or a sliced stream."""
        if isinstance(index, slice):
            return self._new(self.entries[index])
        return self.entries[index]

    def __iter__(self) -> Iterator[EntryT]:
        """Iterate over entries."""
        return iter(self.entries)

    def __len__(self) -> int:
        """Return the number of entries."""
        return len(self.entries)

    def append(self, entry: EntryT) -> Self:
        """Return a new stream with one entry appended."""
        return self._new([*self.entries, entry])

    def extend(self, other: "BaseStream[EntryT] | Iterable[EntryT]") -> Self:
        """Return a new stream with additional entries appended."""
        if isinstance(other, BaseStream):
            type(self)._validate_stream_type(other)
            return self._new([*self.entries, *other.entries])
        return self._new([*self.entries, *other])

    def apply(
        self, transform: Callable[[EntryT], EntryT], where: Callable[[EntryT], bool] | None = None
    ) -> Self:
        """Map entries one-to-one."""
        new_entries = []
        for entry in self.entries:
            if where is None:
                new_entries.append(transform(entry))
            else:
                if where(entry):
                    new_entries.append(transform(entry))
                else:
                    new_entries.append(entry)
        return self._new(new_entries)

    def apply_streamwise(self, fn: Callable[[Self], Self]) -> Self:
        """Apply a transformation to the entire stream object."""
        return fn(self)

    def flat_apply(self, fn: Callable[[EntryT], Iterable[EntryT]]) -> Self:
        """Flat-map entries to zero or more output entries."""
        mapped: list[EntryT] = []
        for entry in self.entries:
            mapped.extend(fn(entry))
        return self._new(mapped)

    def filter_apply(self, fn: Callable[[EntryT], EntryT | None]) -> Self:
        """Map entries while dropping ``None`` results."""
        mapped: list[EntryT] = []
        for entry in self.entries:
            transformed = fn(entry)
            if transformed is not None:
                mapped.append(transformed)
        return self._new(mapped)

    def _filter_where(self, fn: Callable[[EntryT], bool]) -> Self:
        """Return a new stream filtered by predicate."""
        return self._new(entry for entry in self.entries if fn(entry))

    def _filter_by_attrs(self, **attrs: object) -> Self:
        """Return a new stream filtered by exact attribute matches."""

        def matches(entry: EntryT) -> bool:
            for name, value in attrs.items():
                if value is not None and getattr(entry, name) != value:
                    return False
            return True

        return self._filter_where(matches)

    def date_range(self, start: date | None = None, end: date | None = None) -> Self:
        """Filter entries by inclusive date bounds."""
        result = self.entries
        if start is not None:
            result = [entry for entry in result if getattr(entry, "date") >= start]
        if end is not None:
            result = [entry for entry in result if getattr(entry, "date") <= end]
        return self._new(result)

    def _grouped_entries_by_key[KeyT](self, fn: Callable[[EntryT], KeyT]) -> dict[KeyT, list[EntryT]]:
        """Group entries by an arbitrary key function."""
        groups: defaultdict[KeyT, list[EntryT]] = defaultdict(list)
        for entry in self.entries:
            groups[fn(entry)].append(entry)
        return dict(groups)

    def _grouped_entries_by_attr[KeyT](self, attr: str) -> dict[KeyT, list[EntryT]]:
        """Group entries by a named attribute."""
        groups: defaultdict[KeyT, list[EntryT]] = defaultdict(list)
        for entry in self.entries:
            groups[getattr(entry, attr)].append(entry)
        return dict(groups)

    def _grouped_entries_by_period(self, period: Period) -> dict[date, list[EntryT]]:
        """Group entries by normalized period start date."""
        groups: defaultdict[date, list[EntryT]] = defaultdict(list)
        for entry in self.entries:
            groups[period_start(getattr(entry, "date"), period)].append(entry)
        return dict(groups)

    def _grouped_streams[KeyT](self, groups: dict[KeyT, list[EntryT]]) -> dict[KeyT, Self]:
        """Wrap grouped entry lists into same-type stream instances."""
        return {key: self._new(entries) for key, entries in groups.items()}

    @overload
    def sort(self, fn: Callable[[EntryT], SupportsLessThan], *, descending: bool = ...) -> Self: ...

    @overload
    def sort(self, *, attr: str, descending: bool = ...) -> Self: ...

    @overload
    def sort(self) -> Self: ...

    def sort(
        self,
        fn: Callable[[EntryT], SupportsLessThan] | None = None,
        *,
        attr: str | None = None,
        descending: bool = False,
    ) -> Self:
        """Return a new stream sorted by key function or named attribute."""
        if fn is not None and attr is not None:
            raise ValueError("Cannot pass both a key function and 'attr' to sort()")

        if fn is not None:
            return self._new(sorted(self.entries, key=fn, reverse=descending))

        resolved_attr = attr if attr is not None else "date"
        key = self._resolve_sort_key(resolved_attr)
        return self._new(sorted(self.entries, key=key, reverse=descending))

    def scale(self, factor: float) -> Self:
        """Return a new stream with entry amounts scaled by a given factor."""
        raise NotImplementedError

    def sum(self) -> float:
        """Return the sum of entry amounts."""
        return sum((self._amount(entry) for entry in self.entries), start=0.0)

    def count(self) -> int:
        """Return the number of entries."""
        return len(self.entries)


@dataclass
class BaseGroup[KeyT, EntryT, StreamT: _StreamProtocol[EntryT]]:
    """Reusable grouped-container behavior for stream grouping results.

    The group container remains intentionally small: subclasses still own the
    public type identity and any domain-specific grouping semantics, while this
    base class provides the dictionary-like and aggregation behavior.
    """

    groups: dict[KeyT, StreamT]

    def _new(self, groups: dict[KeyT, StreamT]) -> Self:
        """Construct a new instance of the current concrete group type."""
        return type(self)(groups)

    def _empty_stream(self) -> StreamT:
        """Construct an empty stream for empty-group ``ungroup()`` calls."""
        raise NotImplementedError

    def aggregate[T](self, fn: Callable[[StreamT], T]) -> dict[KeyT, T]:
        """Apply a function to each group and return a dict of results."""
        return {key: fn(stream) for key, stream in self.groups.items()}

    def apply_to_groups(
        self,
        fn: Callable[[StreamT], StreamT],
        keys: KeyT | Collection[KeyT] | None = None,
    ) -> Self:
        """Apply a transformation to each selected group."""
        if keys is None:
            transformed_keys = list(self.groups)
        elif isinstance(keys, Collection) and not isinstance(keys, str):
            transformed_keys = list(keys)
            for key in transformed_keys:
                if key not in self.groups:
                    raise ValueError(
                        f"Unknown group key {key!r}. Known group keys: {list(self.groups.keys())}"
                    )
        elif keys in self.groups:
            transformed_keys = [keys]
        else:
            raise ValueError(
                f"Could not interpret keys={keys!r} as valid grouping keys. "
                "Expected None, a single key, or a sequence of keys. "
                f"Valid keys: {list(self.groups.keys())}."
            )

        return self._new(
            {key: fn(stream) if key in transformed_keys else stream for key, stream in self.groups.items()}
        )

    def filter_groups(self, fn: Callable[[KeyT, StreamT], bool]) -> Self:
        """Return only the groups matching the predicate."""
        return self._new({key: stream for key, stream in self.groups.items() if fn(key, stream)})

    def ungroup(self) -> StreamT:
        """Flatten all groups back into a single stream."""
        all_entries: list[EntryT] = []
        for stream in self.groups.values():
            all_entries.extend(stream.entries)

        if not self.groups:
            return self._empty_stream()

        exemplar = next(iter(self.groups.values()))
        return exemplar._new(all_entries)

    def keys(self) -> Iterable[KeyT]:
        """Return the group keys."""
        return self.groups.keys()

    def values(self) -> Iterable[StreamT]:
        """Return grouped streams."""
        return self.groups.values()

    def items(self) -> Iterable[tuple[KeyT, StreamT]]:
        """Return ``(key, stream)`` pairs."""
        return self.groups.items()

    def __getitem__(self, key: KeyT) -> StreamT:
        """Return the stream for a specific grouping key."""
        return self.groups[key]

    def __len__(self) -> int:
        """Return the number of groups."""
        return len(self.groups)

    def __iter__(self) -> Iterator[KeyT]:
        """Iterate over grouping keys."""
        return iter(self.groups)

    def sum(self) -> dict[KeyT, float]:
        """Return the per-group sums."""
        return {key: stream.sum() for key, stream in self.groups.items()}

    def count(self) -> dict[KeyT, int]:
        """Return the per-group entry counts."""
        return {key: stream.count() for key, stream in self.groups.items()}
