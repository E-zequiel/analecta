-- entry_tags only has the composite PK (entry_id, tag_id), which doesn't
-- serve reverse lookups keyed by tag_id alone (get_entry_ids_by_tag,
-- get_hashtag_connections structural peers, get_subgraph neighbor lookup,
-- rename_tag/delete_tag). Purely additive: no data migration needed.
CREATE INDEX idx_entry_tags_tag_id ON entry_tags(tag_id);
