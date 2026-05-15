ALTER TABLE entries ADD COLUMN flags_json TEXT NOT NULL DEFAULT '[]';
UPDATE entries SET flags_json = '["bookmark"]', status = 'unread' WHERE status = 'favorite';
UPDATE entries SET flags_json = '["gem"]',      status = 'unread' WHERE status = 'recommend';
