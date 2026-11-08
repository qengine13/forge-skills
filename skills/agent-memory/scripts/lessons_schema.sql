-- Cross-project lessons layer. Separate table from agent_memory.episodes by design:
-- an episode is an immutable, project-scoped EVENT; a lesson is a curated, cross-project,
-- supersedable ASSERTION. Folding lessons into episodes would add many nullable columns to a
-- populated table and inherit a project-scope CHECK that does not apply to a portable lesson.

-- Self-contained initialization for an empty database, including its namespace.
CREATE SCHEMA IF NOT EXISTS agent_memory;

CREATE TABLE IF NOT EXISTS agent_memory.lessons (
  lesson_id        text PRIMARY KEY,                   -- surrogate: 'lsn_' || sha1(lesson)[:12]
  lesson           text        NOT NULL,               -- the portable assertion (natural key)
  category         text        NOT NULL
                     CHECK (category IN ('cli-tool','platform-gotcha','library-choice',
                                         'provider-behaviour','infra','working-method','dead-end')),
  triggers         jsonb       NOT NULL DEFAULT '[]',  -- keyword surface for the PreToolUse gate
  cost             text        NOT NULL,               -- consequence or cost associated with the lesson
  confidence       text        NOT NULL
                     CHECK (confidence IN ('high','medium','low')),
  -- volatility drives freshness decay: a vendor limit rots, a POSIX behaviour does not
  volatility       text        NOT NULL DEFAULT 'stable'
                     CHECK (volatility IN ('stable','vendor','version')),
  source_project   text        NOT NULL,
  source_ref       text,                               -- episodes.episode_id OR 'file:<name>'
  -- soft reference, NOT a FK: provenance spans two stores (Postgres episodes + file memories)
  superseded_by    text        REFERENCES agent_memory.lessons(lesson_id) ON DELETE SET NULL,
  verified_at      timestamptz NOT NULL DEFAULT now(), -- last time this was confirmed true
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  embedding        bytea,                              -- packed float32[768]; NULL until embedded
  embedding_model  text,
  search_tsv       tsvector GENERATED ALWAYS AS
                     (to_tsvector('english', lesson || ' ' || cost)) STORED
);

CREATE INDEX IF NOT EXISTS idx_lessons_tsv      ON agent_memory.lessons USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_lessons_triggers ON agent_memory.lessons USING gin (triggers);
CREATE INDEX IF NOT EXISTS idx_lessons_category ON agent_memory.lessons (category);
-- the hot path only ever reads live lessons
CREATE INDEX IF NOT EXISTS idx_lessons_live     ON agent_memory.lessons (category)
                                                WHERE superseded_by IS NULL;

CREATE OR REPLACE FUNCTION agent_memory.lessons_touch() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lessons_touch ON agent_memory.lessons;
CREATE TRIGGER trg_lessons_touch BEFORE UPDATE ON agent_memory.lessons
  FOR EACH ROW EXECUTE FUNCTION agent_memory.lessons_touch();
