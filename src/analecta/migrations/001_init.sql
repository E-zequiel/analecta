CREATE TABLE entries (
    id          INTEGER PRIMARY KEY,
    title       TEXT    NOT NULL,
    url         TEXT    NOT NULL UNIQUE,
    file_path   TEXT    NOT NULL,
    source_type TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'unread',
    tags_json   TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE tags (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    count INTEGER DEFAULT 0
);

CREATE TABLE entry_tags (
    entry_id INTEGER REFERENCES entries(id),
    tag_id   INTEGER REFERENCES tags(id),
    PRIMARY KEY (entry_id, tag_id)
);

CREATE VIRTUAL TABLE entries_fts USING fts5(
    title, content, tokenize='unicode61'
);
