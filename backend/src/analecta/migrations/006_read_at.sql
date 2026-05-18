ALTER TABLE entries ADD COLUMN read_at TEXT;
UPDATE entries SET read_at = updated_at WHERE status = 'read';
