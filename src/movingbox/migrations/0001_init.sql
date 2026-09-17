-- Initial schema for the moving box tracker.
--
-- Note on the two location fields on `boxes`: `destination_room_id` is where a
-- box is *going*, `current_location` is where it physically *is* right now
-- (truck, garage stack 3, storage unit). They are deliberately separate --
-- boxes sit in staging areas for days, and conflating the two breaks the main
-- question this tool exists to answer.

CREATE TABLE rooms (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL DEFAULT 'destination'
                        CHECK (kind IN ('source', 'destination', 'both')),
    color_hex   TEXT,                       -- used by the web UI only; labels are mono
    sort_order  INTEGER NOT NULL DEFAULT 0,
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE boxes (
    id                  INTEGER PRIMARY KEY,
    code                TEXT    NOT NULL UNIQUE,   -- 'B-0042', printed on the label
    destination_room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    source_room_id      INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    source_location     TEXT,                      -- free text: 'Basement shelf 3'
    content_summary     TEXT,                      -- the line that prints on the label
    notes               TEXT,
    status              TEXT    NOT NULL DEFAULT 'open'
                                CHECK (status IN ('open', 'packed', 'loaded',
                                                  'delivered', 'unpacked')),
    current_location    TEXT,
    fragile             INTEGER NOT NULL DEFAULT 0 CHECK (fragile    IN (0, 1)),
    open_first          INTEGER NOT NULL DEFAULT 0 CHECK (open_first IN (0, 1)),
    heavy               INTEGER NOT NULL DEFAULT 0 CHECK (heavy      IN (0, 1)),
    weight_kg           REAL,
    group_name          TEXT,                      -- 'box 3 of 5' grouping
    group_index         INTEGER,
    group_total         INTEGER,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    sealed_at           TEXT,
    label_printed_at    TEXT,
    label_print_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_boxes_status   ON boxes(status);
CREATE INDEX idx_boxes_dest     ON boxes(destination_room_id);
CREATE INDEX idx_boxes_location ON boxes(current_location);

CREATE TABLE items (
    id         INTEGER PRIMARY KEY,
    box_id     INTEGER NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    qty        INTEGER NOT NULL DEFAULT 1,
    category   TEXT,
    est_value  REAL,
    notes      TEXT,
    -- Provenance matters: items drafted by a vision model must stay
    -- distinguishable from items a human typed.
    source     TEXT    NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'ai')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_items_box ON items(box_id);

CREATE TABLE photos (
    id             INTEGER PRIMARY KEY,
    box_id         INTEGER NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    thumb_filename TEXT,
    width          INTEGER,
    height         INTEGER,
    bytes          INTEGER,
    sha256         TEXT    NOT NULL,
    caption        TEXT,
    is_primary     INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    taken_at       TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Re-uploading the same shot (a retried upload) must not duplicate it.
    UNIQUE (box_id, sha256)
);

CREATE INDEX idx_photos_box ON photos(box_id);

CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    box_id     INTEGER REFERENCES boxes(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,        -- 'status', 'location', 'print', 'scan', 'create'
    from_value TEXT,
    to_value   TEXT,
    actor      TEXT,
    note       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_events_box ON events(box_id, created_at);

CREATE TABLE ai_jobs (
    id             INTEGER PRIMARY KEY,
    box_id         INTEGER REFERENCES boxes(id) ON DELETE CASCADE,
    provider       TEXT    NOT NULL,     -- 'ollama' | 'claude'
    model          TEXT    NOT NULL,
    prompt_version TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'running', 'done', 'error')),
    raw_response   TEXT,
    error          TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at   TEXT
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Monotonic counters. Box codes are allocated from here rather than from
-- boxes.id so that deleting a box never frees its code for reuse -- a reused
-- code would point an already-printed label at the wrong box.
CREATE TABLE counters (
    name  TEXT    PRIMARY KEY,
    value INTEGER NOT NULL
);

-- Full-text search. Content-owning (not external-content) and maintained
-- explicitly by search.reindex_box() rather than by triggers, because the
-- indexed text is assembled from four tables and is far easier to test as a
-- plain function. rowid is always boxes.id.
CREATE VIRTUAL TABLE box_fts USING fts5(
    code,
    summary,
    notes,
    items,
    photo_captions,
    rooms,
    location,
    tokenize = 'unicode61 remove_diacritics 2'
);
