-- Official image runs this only on a NEW cluster. No history import or Alembic here.
\set VERBOSITY terse
SET log_min_error_statement = 'panic';
SET log_error_verbosity = 'terse';
DO $$
DECLARE app_secret text; migration_secret text;
BEGIN
  app_secret := btrim(pg_read_file('/run/secrets/postgres_app_password'), chr(10)||chr(13)||chr(9)||' ');
  migration_secret := btrim(pg_read_file('/run/secrets/postgres_migrator_password'), chr(10)||chr(13)||chr(9)||' ');
  IF app_secret !~ '^[0-9a-f]{64}$' OR migration_secret !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Explicit generated database credentials required';
  END IF;
  EXECUTE format('CREATE ROLE atlas_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', app_secret);
  EXECUTE format('CREATE ROLE atlas_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', migration_secret);
END $$;
REVOKE ALL ON DATABASE nova_atlas FROM PUBLIC;
REVOKE ALL ON DATABASE postgres FROM PUBLIC;
REVOKE ALL ON DATABASE template1 FROM PUBLIC;
GRANT CONNECT ON DATABASE nova_atlas TO atlas_app, atlas_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA atlas AUTHORIZATION atlas_migrator;
CREATE SCHEMA atlas_meta AUTHORIZATION atlas_migrator;
GRANT USAGE ON SCHEMA atlas TO atlas_app;
ALTER ROLE atlas_app IN DATABASE nova_atlas SET search_path=atlas,pg_catalog;
ALTER ROLE atlas_migrator IN DATABASE nova_atlas SET search_path=atlas,pg_catalog;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migrator IN SCHEMA atlas GRANT SELECT, INSERT ON TABLES TO atlas_app;

-- Last statement only: fail closed on subsequent startup after partial bootstrap.
COPY (SELECT 'atlas-bootstrap-v1') TO '/var/lib/postgresql/18/docker/atlas-bootstrap-complete';
