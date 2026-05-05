"""Tag registry and co-occurrence graph — M6 PKM layer."""

import json

from analecta.storage.index import VaultIndex


def get_backlinks(tag: str, index: VaultIndex) -> list[int]:
    """Return IDs of entries that carry *tag*.

    Args:
        tag: Normalized tag name.
        index: Open ``VaultIndex`` instance.

    Returns:
        List of entry IDs. Empty if no entries carry the tag.
    """
    return index.get_entry_ids_by_tag(tag)


def get_cooccurrences(tag: str, index: VaultIndex) -> dict[str, int]:
    """Return co-occurrence counts for tags that appear alongside *tag*.

    For each entry carrying *tag*, every other tag on that entry is counted.
    The result can be used to build a tag co-occurrence graph.

    Args:
        tag: Normalized tag name to compute co-occurrences for.
        index: Open ``VaultIndex`` instance.

    Returns:
        ``{other_tag: count}`` mapping, sorted by count descending. Empty if
        *tag* appears alone or does not exist.
    """
    entry_ids = index.get_entry_ids_by_tag(tag)
    counts: dict[str, int] = {}
    for eid in entry_ids:
        entry = index.get_entry(eid)
        if entry is None:
            continue
        for other in json.loads(entry.tags_json):
            if other != tag:
                counts[other] = counts.get(other, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
