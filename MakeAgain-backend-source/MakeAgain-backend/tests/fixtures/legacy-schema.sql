-- B07 pre-migration schema only. No production rows or content.
CREATE TABLE activation_redemptions (
	code VARCHAR(64) NOT NULL, 
	used_by VARCHAR(64) NOT NULL, 
	used_at DATETIME NOT NULL, 
	PRIMARY KEY (code)
);

CREATE TABLE auth_sessions (
	token VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	expires_at DATETIME, 
	PRIMARY KEY (token)
);

CREATE TABLE chat_messages (
	id INTEGER NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE companions (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	relationship_type VARCHAR(16), 
	relationship_type_source VARCHAR(16), 
	relationship_type_confidence FLOAT, 
	subject_name VARCHAR(64), 
	started_at DATETIME NOT NULL, 
	ending_stage VARCHAR(16) NOT NULL, 
	ending_started_at DATETIME, 
	ending_ritual VARCHAR(16), 
	ending_committed_at DATETIME, 
	archived_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE daily_chat_summaries (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	date_key VARCHAR(16) NOT NULL, 
	summary TEXT NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_chat_summary UNIQUE (user_id, date_key)
);

CREATE TABLE daily_picks (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	date_key VARCHAR(16) NOT NULL, 
	theme_key VARCHAR(64) NOT NULL, 
	theme_title VARCHAR(64) NOT NULL, 
	opening TEXT NOT NULL, 
	theme_guide TEXT NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_pick UNIQUE (user_id, date_key)
);

CREATE TABLE diary_notes (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	companion_id VARCHAR(64), 
	content TEXT NOT NULL, 
	emotion VARCHAR(32), 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE echo_entries (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	content TEXT NOT NULL, 
	emotion VARCHAR(32), 
	source VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	reveal_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	delivered_at DATETIME, 
	PRIMARY KEY (id)
);

CREATE TABLE emotion_nodes (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	"trigger" VARCHAR(128) NOT NULL, 
	emotion VARCHAR(32) NOT NULL, 
	intensity FLOAT NOT NULL, 
	frequency INTEGER NOT NULL, 
	place VARCHAR(64), 
	time_tag VARCHAR(64), 
	last_seen DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE feedback_entries (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64), 
	content TEXT NOT NULL, 
	contact VARCHAR(128), 
	status VARCHAR(16) NOT NULL, 
	reply TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE interview_sessions (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	loss_type VARCHAR(16), 
	status VARCHAR(16) NOT NULL, 
	state JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE item_memories (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	companion_id VARCHAR(64), 
	item_name VARCHAR(128) NOT NULL, 
	intent VARCHAR(16) NOT NULL, 
	description TEXT NOT NULL, 
	label VARCHAR(64), 
	original_key VARCHAR(256) NOT NULL, 
	cutout_key VARCHAR(256) NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE memory_stream (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	type VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	summary TEXT, 
	facts JSON NOT NULL, 
	emotion JSON, 
	cbt JSON, 
	importance FLOAT NOT NULL, 
	embedding TEXT, 
	place_tag VARCHAR(64), 
	time_tag VARCHAR(64), 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE nudge_logs (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	rule_key VARCHAR(128) NOT NULL, 
	date_key VARCHAR(16) NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_nudge_daily UNIQUE (user_id, rule_key, date_key)
);

CREATE TABLE onboarding_states (
	user_id VARCHAR(64) NOT NULL, 
	phase VARCHAR(16) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id)
);

CREATE TABLE photo_memories (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	companion_id VARCHAR(64), 
	title VARCHAR(128) NOT NULL, 
	description TEXT NOT NULL, 
	photo_key VARCHAR(256) NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE portraits (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	content JSON NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	version INTEGER NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE reports (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	companion_id VARCHAR(64), 
	kind VARCHAR(16) NOT NULL, 
	content JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE state_snapshots (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	date_key VARCHAR(16) NOT NULL, 
	stage INTEGER NOT NULL, 
	stage_label VARCHAR(16) NOT NULL, 
	baseline FLOAT NOT NULL, 
	trend FLOAT NOT NULL, 
	volatility FLOAT NOT NULL, 
	acute_ratio FLOAT NOT NULL, 
	calm_ratio FLOAT NOT NULL, 
	reconcile FLOAT NOT NULL, 
	risk FLOAT NOT NULL, 
	n_days INTEGER NOT NULL, 
	n_memories INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_state_snapshot UNIQUE (user_id, date_key)
);

CREATE TABLE todo_items (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	content VARCHAR(256) NOT NULL, 
	done BOOLEAN NOT NULL, 
	ts DATETIME NOT NULL, 
	done_at DATETIME, 
	PRIMARY KEY (id)
);

CREATE TABLE treehole_letters (
	id VARCHAR(64) NOT NULL, 
	author_user_id VARCHAR(64) NOT NULL, 
	content TEXT NOT NULL, 
	summary TEXT, 
	loss_type VARCHAR(16), 
	emotion VARCHAR(32), 
	tags JSON NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE treehole_replies (
	id VARCHAR(64) NOT NULL, 
	letter_id VARCHAR(64) NOT NULL, 
	author_user_id VARCHAR(64) NOT NULL, 
	content TEXT NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	reviewed_at DATETIME, 
	PRIMARY KEY (id)
);

CREATE TABLE treehole_seen (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	ref_id VARCHAR(64), 
	seen_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_treehole_seen UNIQUE (user_id, kind, ref_id)
);

CREATE TABLE user_auth (
	user_id VARCHAR(64) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	password_salt VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE user_states (
	user_id VARCHAR(64) NOT NULL, 
	current_companion_id VARCHAR(64), 
	relationship_type VARCHAR(16), 
	relationship_type_source VARCHAR(16), 
	relationship_type_confidence FLOAT, 
	subject_name VARCHAR(64), 
	first_letter_status VARCHAR(16) NOT NULL, 
	first_report_status VARCHAR(16) NOT NULL, 
	home_intro_seen BOOLEAN NOT NULL, 
	board_intro_seen BOOLEAN NOT NULL, 
	tts_enabled BOOLEAN NOT NULL, 
	tts_intro_seen BOOLEAN NOT NULL, 
	tts_speed FLOAT NOT NULL, 
	tts_vol FLOAT NOT NULL, 
	echo_enabled BOOLEAN NOT NULL, 
	dismissed_moods JSON NOT NULL, 
	pending_events JSON NOT NULL, 
	ending_stage VARCHAR(16) NOT NULL, 
	ending_started_at DATETIME, 
	ending_ritual VARCHAR(16), 
	ending_committed_at DATETIME, 
	archived_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE users (
	id VARCHAR(64) NOT NULL, 
	loss_type VARCHAR(16), 
	goal VARCHAR(256), 
	nickname VARCHAR(64), 
	membership VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE weekly_reports (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	week_key VARCHAR(16) NOT NULL, 
	content JSON NOT NULL, 
	seen_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_weekly_report UNIQUE (user_id, week_key)
);

CREATE INDEX ix_activation_redemptions_used_by ON activation_redemptions (used_by);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE INDEX ix_chat_messages_user_ts ON chat_messages (user_id, ts);

CREATE INDEX ix_companions_user ON companions (user_id);

CREATE INDEX ix_companions_user_id ON companions (user_id);

CREATE INDEX ix_daily_chat_summaries_user_id ON daily_chat_summaries (user_id);

CREATE INDEX ix_daily_picks_user_id ON daily_picks (user_id);

CREATE INDEX ix_diary_notes_companion_id ON diary_notes (companion_id);

CREATE INDEX ix_diary_notes_user_id ON diary_notes (user_id);

CREATE INDEX ix_echo_entries_user_id ON echo_entries (user_id);

CREATE INDEX ix_emotion_nodes_user_id ON emotion_nodes (user_id);

CREATE INDEX ix_feedback_entries_user_id ON feedback_entries (user_id);

CREATE INDEX ix_interview_sessions_user_id ON interview_sessions (user_id);

CREATE INDEX ix_item_memories_companion_id ON item_memories (companion_id);

CREATE INDEX ix_item_memories_user_id ON item_memories (user_id);

CREATE INDEX ix_memory_stream_user_id ON memory_stream (user_id);

CREATE INDEX ix_nudge_logs_user_id ON nudge_logs (user_id);

CREATE INDEX ix_photo_memories_companion_id ON photo_memories (companion_id);

CREATE INDEX ix_photo_memories_user_id ON photo_memories (user_id);

CREATE INDEX ix_portraits_user_id ON portraits (user_id);

CREATE INDEX ix_reports_companion_id ON reports (companion_id);

CREATE INDEX ix_reports_user_id ON reports (user_id);

CREATE INDEX ix_state_snapshots_user_id ON state_snapshots (user_id);

CREATE INDEX ix_todo_items_user_id ON todo_items (user_id);

CREATE INDEX ix_treehole_letters_author_user_id ON treehole_letters (author_user_id);

CREATE INDEX ix_treehole_replies_author_user_id ON treehole_replies (author_user_id);

CREATE INDEX ix_treehole_replies_letter_id ON treehole_replies (letter_id);

CREATE INDEX ix_treehole_seen_user_id ON treehole_seen (user_id);

CREATE UNIQUE INDEX ix_user_auth_username ON user_auth (username);

CREATE INDEX ix_weekly_reports_user_id ON weekly_reports (user_id);
