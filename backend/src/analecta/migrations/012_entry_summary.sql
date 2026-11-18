-- Bot-side summary mirrored into the DB for UI listings (T8/D11/S14).
-- The frontmatter summary/summary_basis pair remains the agent-facing
-- record (D9); the DB copy lets listings render the summary without
-- reading every file.
ALTER TABLE entries ADD COLUMN summary TEXT;
ALTER TABLE entries ADD COLUMN summary_basis TEXT;
