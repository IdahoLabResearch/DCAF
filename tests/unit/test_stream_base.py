# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Property tests for the collection behavior of BaseStream and BaseGroup in isolation.

The base classes are exercised through minimal subclasses whose entries are plain ``int`` values,
so no domain type's validation or overrides take part. Every property is checked against the
equivalent plain ``list`` or ``dict`` operation, so the oracle is Python's own sequence semantics.
Predicates, keys, and transforms are drawn as arbitrary pure functions of an entry.
"""

from dataclasses import dataclass
from operator import attrgetter
from typing import Any, Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dcaf.streams.base import BaseGroup, BaseStream


@dataclass
class IntStream(BaseStream[int]):
    """Minimal concrete stream of ``int`` entries."""


@dataclass
class OtherIntStream(BaseStream[int]):
    """A second concrete stream type, which must not combine with ``IntStream``."""


@dataclass
class IntGroup(BaseGroup[Any, int, IntStream]):
    """Minimal concrete group of ``IntStream`` values."""

    def _empty_stream(self) -> IntStream:
        return IntStream()


ENTRIES = st.integers()
# "ab" is a group key that a single-key selection must not iterate as the collection {"a", "b"}.
GROUP_KEYS = st.sampled_from([0, 1, "a", "ab"])
# Few distinct values, so sorting regularly sees ties between distinct entries.
SORT_KEYS = st.integers(min_value=0, max_value=3)
# int attributes usable as sort keys; ``imag`` and ``denominator`` make every entry tie.
SORT_ATTRS = st.sampled_from(["real", "imag", "numerator", "denominator"])

# Repeats entries often, since duplicates must survive every operation.
entry_lists = st.lists(ENTRIES, max_size=4).flatmap(
    lambda pool: st.lists(st.sampled_from(pool), max_size=8) if pool else st.just([])
)
streams = entry_lists.map(IntStream)
groups = st.dictionaries(GROUP_KEYS, streams, max_size=4).map(IntGroup)


def entry_functions(returns: st.SearchStrategy[Any]) -> st.SearchStrategy[Callable[[int], Any]]:
    """Draw an arbitrary pure function of one entry."""
    return st.functions(like=lambda entry: None, returns=returns, pure=True)


# === sequence protocol ===


@given(entry_lists)
def test_sequence_protocol_matches_entry_list(entries):
    """Iteration, length, count, and truthiness all report the underlying entry list."""
    stream = IntStream(entries)

    assert list(stream) == entries
    assert len(stream) == stream.count() == len(entries)
    assert bool(stream) is bool(entries)


@given(entry_lists, st.data())
def test_indexing_and_slicing_match_list(entries, data):
    """Integer indices return the list's entry; any slice returns a same-type stream of the
    list's slice, including negative, stepped, and reversed slices."""
    stream = IntStream(entries)
    selection = data.draw(st.slices(len(entries)))

    sliced = stream[selection]

    assert type(sliced) is IntStream
    assert sliced.entries == entries[selection]
    for index in range(-len(entries), len(entries)):
        assert stream[index] == entries[index]


# === concatenation ===

# A from_streams source paired with the entries it contributes.
sources = st.one_of(
    entry_lists.map(lambda entries: (IntStream(entries), entries)),
    entry_lists.map(lambda entries: (tuple(entries), entries)),
    entry_lists.map(lambda entries: (iter(entries), entries)),
)


@given(st.lists(sources, max_size=4))
def test_from_streams_concatenates_sources_in_order(drawn):
    """Streams and arbitrary iterables, including one-shot iterators, concatenate in argument
    order, keeping duplicates. Matching list concatenation also makes the operation associative
    with the empty stream as identity."""
    combined = IntStream.from_streams(*(source for source, _ in drawn))

    assert type(combined) is IntStream
    assert combined.entries == [entry for _, entries in drawn for entry in entries]


@given(entry_lists, entry_lists, ENTRIES)
def test_append_and_extend_match_list_concatenation(entries, others, entry):
    """append adds one entry at the end; extend adds a stream's or an iterable's entries."""
    stream = IntStream(entries)

    assert stream.append(entry).entries == [*entries, entry]
    assert stream.extend(IntStream(others)).entries == entries + others
    assert stream.extend(tuple(others)).entries == entries + others


@given(streams, entry_lists)
def test_combining_with_another_stream_type_raises(stream, others):
    """from_streams and extend reject a stream of a different concrete type, even with the same
    entry type."""
    with pytest.raises(TypeError, match="Cannot combine IntStream with OtherIntStream"):
        IntStream.from_streams(stream, OtherIntStream(others))
    with pytest.raises(TypeError, match="Cannot combine IntStream with OtherIntStream"):
        stream.extend(OtherIntStream(others))


# === non-mutation ===


@given(entry_lists, st.data())
def test_operations_return_new_stream_and_leave_source_unchanged(entries, data):
    """Every stream-returning operation returns a new stream of the same concrete type, with its
    own entry list, and leaves the source's entries unchanged."""
    stream = IntStream(entries)
    snapshot = list(entries)
    predicate = data.draw(entry_functions(st.booleans()))
    transform = data.draw(entry_functions(ENTRIES))
    expansion = data.draw(entry_functions(st.lists(ENTRIES, max_size=3)))
    replacement = data.draw(entry_functions(st.none() | ENTRIES))
    sort_key = data.draw(entry_functions(SORT_KEYS))
    group_key = data.draw(entry_functions(GROUP_KEYS))
    entry = data.draw(ENTRIES)
    selection = data.draw(st.slices(len(entries)))
    operations: dict[str, Callable[[IntStream], IntStream]] = {
        "getitem_slice": lambda s: s[selection],
        "append": lambda s: s.append(entry),
        "extend": lambda s: s.extend([entry]),
        "apply": lambda s: s.apply(transform),
        "apply_where": lambda s: s.apply(transform, where=predicate),
        "flat_apply": lambda s: s.flat_apply(expansion),
        "filter_apply": lambda s: s.filter_apply(replacement),
        "filter_where": lambda s: s._filter_where(predicate),
        "sort": lambda s: s.sort(sort_key),
        "sort_attr": lambda s: s.sort(attr="real"),
    }

    for name, operation in operations.items():
        result = operation(stream)
        assert type(result) is IntStream, name
        assert result.entries is not stream.entries, name
        assert stream.entries == snapshot, name
    for grouped in stream._grouped_streams(stream._grouped_entries_by_key(group_key)).values():
        assert grouped.entries is not stream.entries
    assert stream.entries == snapshot


# === entry-wise transformations ===


@given(entry_lists, entry_functions(st.booleans()))
def test_filter_where_keeps_matching_entries_in_order(entries, predicate):
    """_filter_where keeps exactly the entries the predicate accepts, in their original order, so
    filtering by a predicate and by its negation partitions the stream."""
    filtered = IntStream(entries)._filter_where(predicate)

    assert filtered.entries == [entry for entry in entries if predicate(entry)]


@given(entry_lists, entry_functions(ENTRIES), st.none() | entry_functions(st.booleans()))
def test_apply_transforms_selected_entries_in_place(entries, transform, where):
    """apply replaces each selected entry with its transform at the same position and passes
    unselected entries through; without ``where`` every entry is selected."""
    applied = IntStream(entries).apply(transform, where=where)

    assert applied.entries == [
        transform(entry) if where is None or where(entry) else entry for entry in entries
    ]


@given(entry_lists, entry_functions(st.lists(ENTRIES, max_size=3)))
def test_flat_apply_concatenates_outputs_in_order(entries, expansion):
    """flat_apply emits each entry's outputs in order, input by input, so mapping to ``[entry]``
    is the identity and mapping to ``[]`` empties the stream."""
    flattened = IntStream(entries).flat_apply(expansion)

    assert flattened.entries == [output for entry in entries for output in expansion(entry)]


@given(entry_lists, entry_functions(st.none() | ENTRIES))
def test_filter_apply_keeps_non_none_results_in_order(entries, replacement):
    """filter_apply keeps each non-None result in input order, so returning the entry or None
    by a predicate is the same as filtering by that predicate."""
    kept = IntStream(entries).filter_apply(replacement)

    results = [replacement(entry) for entry in entries]
    assert kept.entries == [result for result in results if result is not None]


@given(streams, streams)
def test_apply_streamwise_returns_the_function_result(stream, other):
    """apply_streamwise passes the whole stream to the function once and returns its result."""
    received = []

    result = stream.apply_streamwise(lambda s: received.append(s) or other)

    assert result is other
    assert len(received) == 1 and received[0] is stream


# === sorting ===


@given(entry_lists, entry_functions(SORT_KEYS), st.booleans())
def test_sort_by_key_matches_stable_sorted(entries, key, descending):
    """sort orders like Python's stable ``sorted``. Ties keep their input order even when
    descending, so a descending sort is not the reverse of an ascending one."""
    ordered = IntStream(entries).sort(key, descending=descending)

    assert ordered.entries == sorted(entries, key=key, reverse=descending)


@given(entry_lists, SORT_ATTRS, st.booleans())
def test_sort_by_attr_matches_stable_sorted(entries, attr, descending):
    """sort(attr=...) orders like a stable ``sorted`` keyed on that entry attribute."""
    ordered = IntStream(entries).sort(attr=attr, descending=descending)

    assert ordered.entries == sorted(entries, key=attrgetter(attr), reverse=descending)


@given(streams, entry_functions(SORT_KEYS))
def test_sort_requires_exactly_one_of_key_function_or_attr(stream, key):
    """A key function and a named attribute cannot be combined, and the base class has no default
    sort key to fall back on when neither is given."""
    with pytest.raises(ValueError, match="Cannot pass both"):
        stream.sort(key, attr="real")
    with pytest.raises(ValueError, match="requires a key function or 'attr'"):
        stream.sort()


# === grouping streams by key ===


def _expected_groups(entries: list[int], key: Callable[[int], Any]) -> dict[Any, list[int]]:
    """Group entries by key, ordering groups by each key's first appearance."""
    expected: dict[Any, list[int]] = {}
    for entry in entries:
        expected.setdefault(key(entry), []).append(entry)
    return expected


@given(entry_lists, entry_functions(GROUP_KEYS))
def test_grouping_by_key_partitions_entries(entries, key):
    """Grouping puts each entry, duplicates included, in its key's group, in input order, with
    groups ordered by each key's first appearance and wrapped as same-type streams."""
    stream = IntStream(entries)

    grouped = stream._grouped_streams(stream._grouped_entries_by_key(key))

    assert all(type(group) is IntStream for group in grouped.values())
    assert {k: group.entries for k, group in grouped.items()} == _expected_groups(entries, key)
    assert list(grouped) == list(_expected_groups(entries, key))


# === BaseGroup ===


def _entries_by_key(group: IntGroup) -> dict[Any, list[int]]:
    return {key: stream.entries for key, stream in group.groups.items()}


@given(groups)
def test_group_reads_like_its_dict(group):
    """Keys, values, items, indexing, iteration, and length all report the underlying dict, in
    its order, and per-group counts match each stream."""
    assert len(group) == len(group.groups)
    assert list(group) == list(group.keys()) == list(group.groups)
    assert list(group.values()) == list(group.groups.values())
    assert list(group.items()) == list(group.groups.items())
    for key, stream in group.groups.items():
        assert group[key] is stream
    assert group.count() == {key: len(stream.entries) for key, stream in group.groups.items()}


@given(groups)
def test_ungroup_concatenates_groups_in_order(group):
    """ungroup concatenates every group's entries in group order into one same-type stream,
    keeping entries repeated across groups; an empty group container ungroups to an empty
    stream."""
    flattened = group.ungroup()

    assert type(flattened) is IntStream
    assert flattened.entries == [
        entry for stream in group.groups.values() for entry in stream.entries
    ]


@given(groups)
def test_aggregate_maps_every_group(group):
    """aggregate applies the function to each group's stream, keyed and ordered like the groups."""
    assert group.aggregate(lambda stream: tuple(stream.entries)) == {
        key: tuple(entries) for key, entries in _entries_by_key(group).items()
    }


@given(groups, st.data())
def test_apply_to_groups_transforms_only_selected_groups(group, data):
    """apply_to_groups transforms the groups selected by one key, a collection of keys, or None
    for all, passes the rest through, and returns the same group type."""
    present = list(group.groups)
    selection = data.draw(
        st.one_of(
            st.none(),
            st.sampled_from(present) if present else st.nothing(),
            st.lists(st.sampled_from(present), unique=True) if present else st.just([]),
        )
    )
    if selection is None:
        selected = present
    elif isinstance(selection, list):
        selected = selection
    else:
        selected = [selection]

    result = group.apply_to_groups(lambda stream: stream[::-1], keys=selection)

    assert type(result) is IntGroup
    assert _entries_by_key(result) == {
        key: entries[::-1] if key in selected else entries
        for key, entries in _entries_by_key(group).items()
    }


@given(groups)
def test_apply_to_groups_rejects_missing_keys(group):
    """Selecting a key that has no group raises ValueError, alone or within a collection."""
    for missing in {0, 1, "a", "ab"} - set(group.groups):
        with pytest.raises(ValueError):
            group.apply_to_groups(lambda stream: stream, keys=missing)
        with pytest.raises(ValueError):
            group.apply_to_groups(lambda stream: stream, keys=[missing])


@given(groups, st.functions(like=lambda key, entries: True, returns=st.booleans(), pure=True))
def test_filter_groups_keeps_matching_groups_in_order(group, keep):
    """filter_groups keeps the groups whose key and stream satisfy the predicate, in order."""
    kept = group.filter_groups(lambda key, stream: keep(key, tuple(stream.entries)))

    assert type(kept) is IntGroup
    assert _entries_by_key(kept) == {
        key: entries for key, entries in _entries_by_key(group).items() if keep(key, tuple(entries))
    }
