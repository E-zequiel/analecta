CREATE TABLE backlink_refs (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    target_text TEXT    NOT NULL,
    is_hashtag  INTEGER NOT NULL DEFAULT 0,
    heading     TEXT,
    pre         TEXT    NOT NULL DEFAULT '',
    highlight   TEXT    NOT NULL DEFAULT '',
    post        TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX idx_backlink_refs_source ON backlink_refs(source_id);
CREATE INDEX idx_backlink_refs_target ON backlink_refs(target_text);
