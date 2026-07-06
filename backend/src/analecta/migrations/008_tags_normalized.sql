-- Adds a case-fold identity column for tags so structural tags ("Python")
-- and content hashtags (#python, #PYTHON) resolve to the same tag.
-- Backfill + case-duplicate merge happens in a one-time Python bootstrap
-- (VaultIndex._bootstrap_tag_normalization), since it needs real entry_tags
-- counts and tags_json rewrites that plain SQL can't express safely.
-- The unique index on `normalized` is created by that same bootstrap, only
-- after duplicates are merged — never here, or it collides on pre-existing
-- case variants.
ALTER TABLE tags ADD COLUMN normalized TEXT NOT NULL DEFAULT '';

-- Superseded by the live union query in VaultIndex.list_tags(); never read
-- since the tags/content-hashtag unification (see feedback_two_tag_systems).
ALTER TABLE tags DROP COLUMN count;
