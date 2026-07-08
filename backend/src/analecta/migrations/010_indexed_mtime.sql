-- Tracks the file mtime backlink_refs/FTS content was last derived from,
-- so a reconciliation sweep (VaultIndex.reconcile_stale_entries) can
-- detect entries edited outside the app and reindex only those. NULL for
-- pre-existing rows — the first sweep after upgrading treats every entry
-- as unverified and reindexes the whole vault once. Purely additive: no
-- data migration needed.
ALTER TABLE entries ADD COLUMN indexed_mtime REAL;
