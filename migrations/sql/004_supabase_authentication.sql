-- Supabase authentication upgrade for the existing application database.
-- Run the ENTIRE file in the test project's Supabase SQL Editor as schema owner.
-- Preserves existing admin IDs, roles, shipments, documents and audit history.
-- It does not create Auth users, link accounts automatically, or store passwords.
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
SELECT pg_advisory_xact_lock(723910042005);
LOCK TABLE admin_users IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE
    previous_digest text;
    users_before bigint;
    sessions_before bigint := 0;
    role_name text;
    linked_column boolean;
    sessions_table boolean;
BEGIN
    IF to_regclass('admin_migration_runs') IS NULL THEN
        RAISE EXCEPTION 'Existing migration ledger required. Complete the application database setup first.';
    END IF;
    SELECT target_digest INTO previous_digest FROM admin_migration_runs
        WHERE version = '004_supabase_authentication';
    IF previous_digest IS NOT NULL AND previous_digest <> '54fe58f0155de42fb36bea95e146492dbb76b8fa3bfc9c16ef93aaea810f0175' THEN
        RAISE EXCEPTION 'Migration 004 differs from its recorded revision. No records were changed.';
    END IF;
    SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid = 'admin_users'::regclass
        AND attname = 'supabase_user_id' AND NOT attisdropped) INTO linked_column;
    sessions_table := to_regclass('admin_auth_sessions') IS NOT NULL;
    IF previous_digest IS NOT NULL AND (NOT linked_column OR NOT sessions_table) THEN
        RAISE EXCEPTION 'Migration 004 is recorded but its structure is missing. Review the database before retrying.';
    END IF;
    IF linked_column AND NOT EXISTS(SELECT 1 FROM pg_attribute
        WHERE attrelid = 'admin_users'::regclass AND attname = 'supabase_user_id'
        AND atttypid = 'uuid'::regtype AND NOT attnotnull AND NOT atthasdef) THEN
        RAISE EXCEPTION 'Existing supabase_user_id column is incompatible. No records were changed.';
    END IF;
    SELECT count(*) INTO users_before FROM admin_users;
    IF sessions_table THEN
        LOCK TABLE admin_auth_sessions IN ACCESS EXCLUSIVE MODE;
        SELECT count(*) INTO sessions_before FROM admin_auth_sessions;
    END IF;
    ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS supabase_user_id uuid;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid = 'admin_users'::regclass
        AND conname = 'uq_admin_users_supabase_user_id' AND contype = 'u'
        AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
            WHERE attrelid = 'admin_users'::regclass AND attname = 'supabase_user_id')]::smallint[]) THEN
        ALTER TABLE admin_users ADD CONSTRAINT uq_admin_users_supabase_user_id UNIQUE (supabase_user_id);
    END IF;
    CREATE TABLE IF NOT EXISTS admin_auth_sessions (
        session_hash varchar(64) NOT NULL,
        admin_user_id uuid NOT NULL,
        token_data text NOT NULL,
        created_at timestamp with time zone NOT NULL,
        expires_at timestamp with time zone NOT NULL,
        CONSTRAINT pk_admin_auth_sessions PRIMARY KEY (session_hash),
        CONSTRAINT fk_admin_auth_sessions_admin_user_id_admin_users
            FOREIGN KEY (admin_user_id) REFERENCES admin_users(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS ix_admin_auth_sessions_admin_user_id ON admin_auth_sessions(admin_user_id);
    CREATE INDEX IF NOT EXISTS ix_admin_auth_sessions_expires_at ON admin_auth_sessions(expires_at);
    -- Reject an incompatible pre-existing session table rather than replacing it.
    IF EXISTS (
        SELECT 1 FROM (VALUES
            ('session_hash', 'character varying(64)'), ('admin_user_id', 'uuid'),
            ('token_data', 'text'), ('created_at', 'timestamp with time zone'),
            ('expires_at', 'timestamp with time zone')
        ) AS expected(name, kind)
        LEFT JOIN pg_attribute a ON a.attrelid = 'admin_auth_sessions'::regclass
            AND a.attname = expected.name AND NOT a.attisdropped
        WHERE a.attname IS NULL OR format_type(a.atttypid, a.atttypmod) <> expected.kind OR NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'Existing authentication session table is incompatible. No records were changed.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'admin_auth_sessions'::regclass
        AND contype = 'p' AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
            WHERE attrelid = 'admin_auth_sessions'::regclass AND attname = 'session_hash')]::smallint[])
        OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'admin_auth_sessions'::regclass
            AND contype = 'f' AND confrelid = 'admin_users'::regclass AND confdeltype = 'c'
            AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                WHERE attrelid = 'admin_auth_sessions'::regclass AND attname = 'admin_user_id')]::smallint[]
            AND confkey = ARRAY[(SELECT attnum FROM pg_attribute
                WHERE attrelid = 'admin_users'::regclass AND attname = 'id')]::smallint[]) THEN
        RAISE EXCEPTION 'Authentication session keys are incompatible. No records were changed.';
    END IF;
    ALTER TABLE admin_auth_sessions ENABLE ROW LEVEL SECURITY;
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON admin_auth_sessions FROM %I', role_name);
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM admin_users) <> users_before
        OR (SELECT count(*) FROM admin_auth_sessions) <> sessions_before THEN
        RAISE EXCEPTION 'Record preservation check failed. The transaction was rolled back.';
    END IF;
    ALTER TABLE admin_migration_runs ENABLE ROW LEVEL SECURITY;
    IF previous_digest IS NULL THEN
        INSERT INTO admin_migration_runs
            (version, namespace, source_digest, target_digest, source_schema, counts, warnings, completed_at)
        VALUES ('004_supabase_authentication', '82934e67-e25a-5900-bd55-eb8df73a1772'::uuid,
            '861856993ddacbc3b67726df2d332397bb08511b84bf893f3315553a46453617', '54fe58f0155de42fb36bea95e146492dbb76b8fa3bfc9c16ef93aaea810f0175', '{"preserves_admin_ids": true, "upgrade": "supabase_authentication"}'::jsonb,
            jsonb_build_object('admin_users', jsonb_build_object('preserved_rows', users_before),
                'admin_auth_sessions', jsonb_build_object('preserved_rows', sessions_before)),
            '[]'::jsonb, now());
    END IF;
END;
$migration$;
COMMIT;

-- Verification queries (read only).
SELECT version, completed_at FROM admin_migration_runs WHERE version = '004_supabase_authentication';
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema = current_schema() AND table_name = 'admin_users' AND column_name = 'supabase_user_id';
SELECT relname, relrowsecurity FROM pg_class WHERE oid = 'admin_auth_sessions'::regclass;
