-- Log tailer offsets (owner A, spec §2.1). One row per (source, path). The tailer
-- updates `byte_offset` after writing the batch to events.raw; `inode` catches file
-- rotation (same path, new inode -> byte_offset resets to 0).
--
-- `offset` is a reserved word in PostgreSQL (LIMIT ... OFFSET), hence `byte_offset`.

CREATE TABLE tailer_offsets (
    source_id   text        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path        text        NOT NULL,
    inode       bigint,
    byte_offset bigint      NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, path)
);

COMMENT ON COLUMN tailer_offsets.byte_offset IS
    'Byte position in the file, advanced after each successful batch write to events.raw.';
COMMENT ON COLUMN tailer_offsets.inode IS
    'File inode; a change means the file was rotated and byte_offset resets.';