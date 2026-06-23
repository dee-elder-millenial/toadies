-- Toadies SQLite schema.
-- Suggested database path: ~/.local/share/toadies/toadies.db
-- The first section mirrors the handoff `database-schema.sql`; the final section
-- (competency + grades) is the trust-loop addition (see docs/superpowers/specs/).

pragma journal_mode = wal;
pragma foreign_keys = on;

create table if not exists sessions (
  id text primary key,
  codex_session_id text,
  cwd text not null,
  repo_root text,
  budget_mode text not null default 'normal',
  started_at text not null default current_timestamp,
  ended_at text,
  notes text
);

create table if not exists turns (
  id text primary key,
  session_id text references sessions(id) on delete cascade,
  codex_turn_id text,
  role text,
  prompt_hash text,
  prompt_preview text,
  model text,
  created_at text not null default current_timestamp
);

create table if not exists events (
  id text primary key,
  session_id text references sessions(id) on delete set null,
  turn_id text references turns(id) on delete set null,
  event_type text not null,
  toadie text,
  cwd text,
  input_hash text,
  output_hash text,
  metadata_json text not null default '{}',
  created_at text not null default current_timestamp
);

create table if not exists artifacts (
  id text primary key,
  event_id text references events(id) on delete set null,
  kind text not null,
  path text not null,
  sha256 text not null,
  original_chars integer,
  summary_chars integer,
  summary text,
  created_at text not null default current_timestamp
);

create index if not exists idx_artifacts_sha256 on artifacts(sha256);

create table if not exists budget_policies (
  id text primary key,
  name text not null unique,
  daily_paid_call_soft_limit integer not null default 80,
  daily_paid_call_hard_limit integer not null default 120,
  local_first_after_soft_limit integer not null default 1,
  large_context_threshold_chars integer not null default 20000,
  model_policy_json text not null default '{}',
  enabled integer not null default 1,
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp
);

insert or ignore into budget_policies (id, name, model_policy_json)
values (
  'default',
  'default',
  '{"normal":"paid_when_needed","economy":"local_first","critical":"bypass_most_preprocessing"}'
);

create table if not exists repo_files (
  id text primary key,
  repo_root text not null,
  rel_path text not null,
  sha256 text,
  lang text,
  last_seen text not null default current_timestamp,
  summary text,
  symbols_json text not null default '[]',
  unique(repo_root, rel_path)
);

create index if not exists idx_repo_files_repo_path on repo_files(repo_root, rel_path);

create table if not exists memories (
  id text primary key,
  repo_root text,
  key text not null,
  value text not null,
  confidence real not null default 0.5,
  source_event_id text references events(id) on delete set null,
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp,
  unique(repo_root, key)
);

create table if not exists secret_findings (
  id text primary key,
  event_id text references events(id) on delete set null,
  severity text not null,
  kind text not null,
  location text,
  message text not null,
  redacted integer not null default 0,
  created_at text not null default current_timestamp
);

create table if not exists compression_events (
  id text primary key,
  event_id text references events(id) on delete cascade,
  source_hint text,
  original_chars integer not null,
  summary_chars integer not null,
  raw_artifact_id text references artifacts(id) on delete set null,
  created_at text not null default current_timestamp
);

-- ---------------------------------------------------------------------------
-- Trust loop (added 2026-06-23) — competency track record + grade audit trail.
-- ---------------------------------------------------------------------------

create table if not exists competency (
  toadie text not null,
  task_type text not null,
  ema real not null default 0.0,
  samples integer not null default 0,
  leash_level text not null default 'probation',
  updated_at text not null default current_timestamp,
  primary key (toadie, task_type)
);

create table if not exists grades (
  id text primary key,
  toadie text not null,
  task_type text not null,
  score real not null,
  source text not null,
  prompt_hash text,
  output_hash text,
  event_id text references events(id) on delete set null,
  created_at text not null default current_timestamp
);

create index if not exists idx_grades_toadie_task on grades(toadie, task_type);
