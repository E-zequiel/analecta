-- Derived deprecation flag: truth of deprecation lives in the page
-- frontmatter (`status: deprecated`); this column is written through at
-- deprecation time and re-derived from the page by the reconciliation
-- sweep (VaultIndex.reconcile_stale_entries) — the same derived-state
-- pattern as backlink_refs/FTS, gated on entries.indexed_mtime. General
-- derivation: ANY entry whose page frontmatter carries
-- `status: deprecated` derives flag 1. Purely additive: no data
-- migration needed.
ALTER TABLE entries ADD COLUMN deprecated INTEGER NOT NULL DEFAULT 0;
