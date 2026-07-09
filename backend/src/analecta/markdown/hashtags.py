"""Hashtag utilities — M4 pipeline."""

import re

_WHITESPACE_RUN_RE = re.compile(r"\s+")


def title_to_hashtag_key(title: str) -> str:
    """Fold *title* into the identity space a same-spelled hashtag would use.

    This does not strip accents or fold symbols to underscore — a hashtag's
    own identity (``backlink_refs.target_text``) is ``casefold()``-only and
    accent/symbol-*preserving* (see the "Normalization" section in
    ``docs/wikilinks-and-hashtags.md``), so the title side of a
    hashtag-to-title match must use the same rule or
    the two can never agree on titles containing an accent or one of the
    hashtag charset's symbols (``- ' ~ ^``). The one unavoidable exception
    is whitespace: no valid hashtag can contain a space, so a multi-word
    title can only ever be referenced by a hashtag that substitutes
    underscores for spaces — this collapses whitespace runs to a single
    underscore before casefolding, mirroring how the hashtag charset
    already treats underscore as an ordinary continuation character.

    Used by :meth:`~analecta.storage.index.VaultIndex.get_backlinks`,
    :meth:`~analecta.storage.index.VaultIndex.get_outgoing_links`,
    :meth:`~analecta.storage.index.VaultIndex.get_subgraph`, and
    :meth:`~analecta.storage.index.VaultIndex.get_graph` to resolve a
    ``#hashtag`` against an entry title.

    Args:
        title: Entry title to fold.

    Returns:
        *title* with leading/trailing whitespace stripped, internal
        whitespace runs replaced by a single underscore, then casefolded.
        Every other character (accents, hyphens, apostrophes, tildes,
        carets) is preserved literally.
    """
    return _WHITESPACE_RUN_RE.sub("_", title.strip()).casefold()
