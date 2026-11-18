-- Rename the entry read-state column: `status` (unread/read) collided with
-- OKF's `status: draft|stable|deprecated` vocabulary. Read semantics and UI
-- labels are unchanged; only the storage key moves.
ALTER TABLE entries RENAME COLUMN status TO read_state;
