\set ON_ERROR_STOP on

-- Cluster-global identities are operator authority, not Alembic schema state.
-- The caller supplies only the three booleans below; secrets arrive through
-- process-local PGOPTIONS custom settings and are never interpolated by a shell.

\if :connectmd_mutate
DO $operator$
BEGIN
  IF session_user <> 'postgres' OR current_user <> 'postgres' THEN
    RAISE EXCEPTION 'database role reconciliation requires the postgres operator';
  END IF;
END
$operator$;

DO $contract$
DECLARE
  role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'connectmd_migrator',
    'connectmd_api',
    'connectmd_search_projection',
    'connectmd_projection_admin',
    'connectmd_account_erasure',
    'connectmd_backup'
  ] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format(
        'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
        role_name
      );
    END IF;
    EXECUTE format(
      'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1 VALID UNTIL %L',
      role_name,
      'infinity'
    );
    EXECUTE format(
      'ALTER ROLE %I RESET ALL',
      role_name
    );
    EXECUTE format('ALTER ROLE %I SET search_path TO pg_catalog, public', role_name);
  END LOOP;
END
$contract$;

-- Repair authority left by an interrupted or older role-separation attempt.
-- Both identifiers come from PostgreSQL itself and are quoted by format(%I).
SELECT format('ALTER DATABASE %I OWNER TO %I', current_database(), session_user) \gexec

SELECT format('ALTER ROLE connectmd_migrator PASSWORD %L', current_setting('connectmd.migrator_password')) \gexec
SELECT format('ALTER ROLE connectmd_api PASSWORD %L', current_setting('connectmd.api_password')) \gexec
SELECT format('ALTER ROLE connectmd_search_projection PASSWORD %L', current_setting('connectmd.search_projection_password')) \gexec
SELECT format('ALTER ROLE connectmd_projection_admin PASSWORD %L', current_setting('connectmd.projection_admin_password')) \gexec
SELECT format('ALTER ROLE connectmd_account_erasure PASSWORD %L', current_setting('connectmd.account_erasure_password')) \gexec
SELECT format('ALTER ROLE connectmd_backup PASSWORD %L', current_setting('connectmd.backup_password')) \gexec

-- A contract role may neither inherit another role nor be inherited by one.
SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles granted ON granted.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE granted.rolname = ANY (ARRAY[
        'connectmd_migrator','connectmd_api','connectmd_search_projection',
        'connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'
      ])
   OR member.rolname = ANY (ARRAY[
        'connectmd_migrator','connectmd_api','connectmd_search_projection',
        'connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'
      ])
\gexec

REVOKE CONNECT, TEMPORARY ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO
  connectmd_migrator,
  connectmd_api,
  connectmd_search_projection,
  connectmd_projection_admin,
  connectmd_account_erasure,
  connectmd_backup;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO connectmd_migrator;
GRANT USAGE ON SCHEMA public TO
  connectmd_api,
  connectmd_search_projection,
  connectmd_projection_admin,
  connectmd_account_erasure,
  connectmd_backup;

-- Existing objects must become migrator-owned before Alembic can upgrade them.
SELECT format('ALTER TABLE %I.%I OWNER TO connectmd_migrator', n.nspname, c.relname)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
ORDER BY c.relname
\gexec
SELECT format('ALTER SEQUENCE %I.%I OWNER TO connectmd_migrator', n.nspname, c.relname)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'S'
ORDER BY c.relname
\gexec

ALTER DEFAULT PRIVILEGES FOR ROLE connectmd_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE connectmd_migrator IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE connectmd_migrator IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE connectmd_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM
  connectmd_api, connectmd_search_projection, connectmd_projection_admin,
  connectmd_account_erasure, connectmd_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE connectmd_migrator IN SCHEMA public REVOKE ALL ON SEQUENCES FROM
  connectmd_api, connectmd_search_projection, connectmd_projection_admin,
  connectmd_account_erasure, connectmd_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE connectmd_migrator IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM
  connectmd_api, connectmd_search_projection, connectmd_projection_admin,
  connectmd_account_erasure, connectmd_backup;
\endif

\if :connectmd_reconcile
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM
  connectmd_api, connectmd_search_projection, connectmd_projection_admin,
  connectmd_account_erasure, connectmd_backup;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM
  connectmd_api, connectmd_search_projection, connectmd_projection_admin,
  connectmd_account_erasure, connectmd_backup;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM
  connectmd_api, connectmd_search_projection, connectmd_projection_admin,
  connectmd_account_erasure, connectmd_backup;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO connectmd_api;
GRANT SELECT, USAGE ON ALL SEQUENCES IN SCHEMA public TO connectmd_api;

GRANT SELECT ON documents, document_versions TO connectmd_search_projection;
GRANT SELECT, INSERT, UPDATE, DELETE ON search_projection_tasks TO connectmd_search_projection;

GRANT SELECT ON
  alembic_version, documents, document_versions, search_projection_tasks,
  account_lifecycles, account_access_denies,
  public_taxonomy_projection_state, public_taxonomy_terms,
  public_taxonomy_memberships, public_taxonomy_document_snapshots,
  public_exact_search_projection_state, public_exact_search_document_snapshots,
  public_exact_search_compact_values
TO connectmd_projection_admin;
GRANT INSERT, UPDATE, DELETE ON
  public_taxonomy_projection_state, public_taxonomy_terms,
  public_taxonomy_memberships, public_taxonomy_document_snapshots,
  public_exact_search_projection_state, public_exact_search_document_snapshots,
  public_exact_search_compact_values
TO connectmd_projection_admin;
GRANT DELETE ON search_projection_tasks TO connectmd_projection_admin;
GRANT UPDATE (schema_version) ON documents TO connectmd_projection_admin;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO connectmd_account_erasure;
GRANT DELETE ON
  contact_policies, documents, document_versions, posts, post_versions,
  api_keys, agent_grants, agent_mandates, agent_identities,
  connection_requests, connections, conversations, messages,
  organizations, jobs, applications, organization_memberships, agent_proposals,
  account_lifecycle_receipt_rate_limits
TO connectmd_account_erasure;
GRANT INSERT ON
  account_erasure_file_proofs, identifier_reservations,
  search_projection_tasks, account_lifecycle_tombstones
TO connectmd_account_erasure;
GRANT UPDATE ON
  account_erasure_items, account_lifecycles, account_backup_authority,
  account_backup_obligations, search_projection_tasks,
  public_taxonomy_projection_state, public_taxonomy_terms,
  public_taxonomy_memberships, public_taxonomy_document_snapshots,
  public_exact_search_projection_state, public_exact_search_document_snapshots,
  public_exact_search_compact_values
TO connectmd_account_erasure;
GRANT DELETE ON
  public_taxonomy_projection_state, public_taxonomy_terms,
  public_taxonomy_memberships, public_taxonomy_document_snapshots,
  public_exact_search_projection_state, public_exact_search_document_snapshots,
  public_exact_search_compact_values
TO connectmd_account_erasure;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO connectmd_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO connectmd_backup;
\endif

\if :connectmd_verify
-- Bidirectional verification: both missing and surplus privileges are fatal.
DO $verify$
DECLARE
  bad text;
BEGIN
  SELECT string_agg(rolname, ', ' ORDER BY rolname) INTO bad
  FROM pg_roles
  WHERE rolname = ANY (ARRAY[
          'connectmd_migrator','connectmd_api','connectmd_search_projection',
          'connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'
        ])
    AND (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit
         OR rolreplication OR rolbypassrls OR rolpassword IS NULL
         OR rolconnlimit <> -1 OR rolvaliduntil IS NULL OR rolvaliduntil <= now()
         OR COALESCE(rolconfig, ARRAY[]::text[]) <> ARRAY['search_path=pg_catalog, public']);
  IF bad IS NOT NULL THEN RAISE EXCEPTION 'database role attributes/search_path failed: %', bad; END IF;

  IF (SELECT count(*) FROM pg_roles WHERE rolname = ANY (ARRAY[
      'connectmd_migrator','connectmd_api','connectmd_search_projection',
      'connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'])) <> 6 THEN
    RAISE EXCEPTION 'database role contract is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_auth_members m
    JOIN pg_roles granted ON granted.oid=m.roleid
    JOIN pg_roles member ON member.oid=m.member
    WHERE granted.rolname = ANY (ARRAY['connectmd_migrator','connectmd_api','connectmd_search_projection','connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'])
       OR member.rolname = ANY (ARRAY['connectmd_migrator','connectmd_api','connectmd_search_projection','connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'])
  ) THEN RAISE EXCEPTION 'database contract roles must have no memberships'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind NOT IN ('r','p','S','i','I')
  ) THEN RAISE EXCEPTION 'unsupported public schema object kind'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_roles owner ON owner.oid=c.relowner
    WHERE n.nspname='public' AND c.relkind IN ('r','p','S') AND owner.rolname <> 'connectmd_migrator'
  ) THEN RAISE EXCEPTION 'public tables/sequences must be migrator-owned'; END IF;
  IF NOT has_schema_privilege('connectmd_migrator','public','CREATE')
     OR NOT has_schema_privilege('connectmd_migrator','public','USAGE') THEN
    RAISE EXCEPTION 'migrator does not own the public schema';
  END IF;
  IF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public')
       <> 'connectmd_migrator'
     OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())
       <> 'postgres' THEN RAISE EXCEPTION 'database/schema ownership contract failed'; END IF;
  IF NOT has_database_privilege('connectmd_migrator',current_database(),'CONNECT')
     OR has_database_privilege('connectmd_migrator',current_database(),'CREATE')
     OR has_database_privilege('connectmd_migrator',current_database(),'TEMPORARY') THEN
    RAISE EXCEPTION 'migrator database privilege contract failed';
  END IF;
  IF EXISTS (
    SELECT 1 FROM unnest(ARRAY['connectmd_api','connectmd_search_projection','connectmd_projection_admin','connectmd_account_erasure','connectmd_backup']) AS role_list(role_name)
    WHERE NOT has_database_privilege(role_name,current_database(),'CONNECT')
       OR has_database_privilege(role_name,current_database(),'CREATE')
       OR has_database_privilege(role_name,current_database(),'TEMPORARY')
       OR NOT has_schema_privilege(role_name,'public','USAGE')
       OR has_schema_privilege(role_name,'public','CREATE')
  ) THEN RAISE EXCEPTION 'runtime database/schema privilege contract failed'; END IF;
  IF has_database_privilege('connectmd_api',current_database(),'CREATE') THEN
    RAISE EXCEPTION 'runtime database role has database-owner authority';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_database d
    CROSS JOIN LATERAL aclexplode(COALESCE(d.datacl, acldefault('d',d.datdba))) acl
    WHERE d.datname=current_database() AND acl.grantee=0
      AND acl.privilege_type IN ('CONNECT','TEMPORARY')
  ) OR EXISTS (
    SELECT 1 FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n',n.nspowner))) acl
    WHERE n.nspname='public' AND acl.grantee=0
  ) OR EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner))) acl
    WHERE n.nspname='public' AND c.relkind IN ('r','p','S') AND acl.grantee=0
  ) OR EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f',p.proowner))) acl
    WHERE n.nspname='public' AND acl.grantee=0
  ) THEN RAISE EXCEPTION 'PUBLIC retains database or public-schema authority'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f',p.proowner))) acl
    JOIN pg_roles role ON role.oid=acl.grantee
    WHERE n.nspname='public' AND role.rolname = ANY (ARRAY[
      'connectmd_api','connectmd_search_projection','connectmd_projection_admin',
      'connectmd_account_erasure','connectmd_backup'])
  ) THEN RAISE EXCEPTION 'runtime role retains public function authority'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    JOIN pg_roles owner ON owner.oid=p.proowner
    WHERE n.nspname='public' AND owner.rolname <> 'connectmd_migrator'
  ) THEN RAISE EXCEPTION 'public functions must be migrator-owned'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_default_acl d
    JOIN pg_roles owner ON owner.oid=d.defaclrole
    CROSS JOIN LATERAL aclexplode(d.defaclacl) acl
    LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee
    WHERE owner.rolname='connectmd_migrator'
      AND (acl.grantee=0 OR grantee.rolname = ANY (ARRAY[
        'connectmd_api','connectmd_search_projection','connectmd_projection_admin',
        'connectmd_account_erasure','connectmd_backup']))
  ) THEN RAISE EXCEPTION 'migrator default privileges retain runtime or PUBLIC authority'; END IF;
END
$verify$;

CREATE TEMP TABLE connectmd_expected_table_acl(
  role_name text NOT NULL, table_name text NOT NULL, privilege text NOT NULL,
  PRIMARY KEY(role_name,table_name,privilege)
);
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_api', c.relname, privilege
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS privilege_list(privilege)
WHERE n.nspname='public' AND c.relkind IN ('r','p');
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_backup', c.relname, 'SELECT'
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p');
INSERT INTO connectmd_expected_table_acl VALUES
  ('connectmd_search_projection','documents','SELECT'),
  ('connectmd_search_projection','document_versions','SELECT'),
  ('connectmd_search_projection','search_projection_tasks','SELECT'),
  ('connectmd_search_projection','search_projection_tasks','INSERT'),
  ('connectmd_search_projection','search_projection_tasks','UPDATE'),
  ('connectmd_search_projection','search_projection_tasks','DELETE');
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_projection_admin', unnest(ARRAY[
  'alembic_version','documents','document_versions','search_projection_tasks',
  'account_lifecycles','account_access_denies','public_taxonomy_projection_state',
  'public_taxonomy_terms','public_taxonomy_memberships','public_taxonomy_document_snapshots',
  'public_exact_search_projection_state','public_exact_search_document_snapshots',
  'public_exact_search_compact_values']), 'SELECT';
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_projection_admin', unnest(ARRAY[
  'public_taxonomy_projection_state','public_taxonomy_terms','public_taxonomy_memberships',
  'public_taxonomy_document_snapshots','public_exact_search_projection_state',
  'public_exact_search_document_snapshots','public_exact_search_compact_values']), privilege
FROM unnest(ARRAY['INSERT','UPDATE','DELETE']) AS privilege_list(privilege);
INSERT INTO connectmd_expected_table_acl VALUES
  ('connectmd_projection_admin','search_projection_tasks','DELETE');
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_account_erasure', c.relname, 'SELECT'
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p');
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_account_erasure', unnest(ARRAY[
  'contact_policies','documents','document_versions','posts','post_versions','api_keys',
  'agent_grants','agent_mandates','agent_identities','connection_requests','connections',
  'conversations','messages','organizations','jobs','applications','organization_memberships',
  'agent_proposals','account_lifecycle_receipt_rate_limits']), 'DELETE';
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_account_erasure', unnest(ARRAY[
  'account_erasure_file_proofs','identifier_reservations','search_projection_tasks',
  'account_lifecycle_tombstones']), 'INSERT';
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_account_erasure', unnest(ARRAY[
  'account_erasure_items','account_lifecycles','account_backup_authority',
  'account_backup_obligations','search_projection_tasks','public_taxonomy_projection_state',
  'public_taxonomy_terms','public_taxonomy_memberships','public_taxonomy_document_snapshots',
  'public_exact_search_projection_state','public_exact_search_document_snapshots',
  'public_exact_search_compact_values']), 'UPDATE';
INSERT INTO connectmd_expected_table_acl
SELECT 'connectmd_account_erasure', unnest(ARRAY[
  'public_taxonomy_projection_state','public_taxonomy_terms','public_taxonomy_memberships',
  'public_taxonomy_document_snapshots','public_exact_search_projection_state',
  'public_exact_search_document_snapshots','public_exact_search_compact_values']), 'DELETE';

DO $acl_verify$
BEGIN
  IF EXISTS (
    WITH actual AS (
      SELECT role.rolname role_name, c.relname table_name, acl.privilege_type privilege
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r',c.relowner))) acl
      JOIN pg_roles role ON role.oid=acl.grantee
      WHERE n.nspname='public' AND c.relkind IN ('r','p')
        AND role.rolname = ANY (ARRAY['connectmd_api','connectmd_search_projection','connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'])
    )
    (SELECT * FROM connectmd_expected_table_acl EXCEPT SELECT * FROM actual)
    UNION ALL
    (SELECT * FROM actual EXCEPT SELECT * FROM connectmd_expected_table_acl)
  ) THEN RAISE EXCEPTION 'table ACL contract has missing or surplus authority'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(a.attacl) acl
    JOIN pg_roles role ON role.oid=acl.grantee
    WHERE n.nspname='public' AND role.rolname = ANY (ARRAY['connectmd_api','connectmd_search_projection','connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'])
      AND NOT (role.rolname='connectmd_projection_admin' AND c.relname='documents'
               AND a.attname='schema_version' AND acl.privilege_type='UPDATE')
  ) THEN RAISE EXCEPTION 'column ACL contract has surplus authority'; END IF;
  IF NOT has_column_privilege('connectmd_projection_admin','documents','schema_version','UPDATE')
     OR has_table_privilege('connectmd_projection_admin','documents','UPDATE') THEN
    RAISE EXCEPTION 'projection-admin schema-version authority failed'; END IF;
  IF EXISTS (
    WITH expected AS (
      SELECT 'connectmd_api' role_name,c.relname sequence_name,privilege
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN unnest(ARRAY['SELECT','USAGE']) AS privilege_list(privilege)
      WHERE n.nspname='public' AND c.relkind='S'
      UNION ALL
      SELECT 'connectmd_backup',c.relname,'SELECT'
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='S'
    ), actual AS (
      SELECT role.rolname,c.relname,acl.privilege_type
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('S',c.relowner))) acl
      JOIN pg_roles role ON role.oid=acl.grantee
      WHERE n.nspname='public' AND c.relkind='S'
        AND role.rolname = ANY (ARRAY['connectmd_api','connectmd_search_projection','connectmd_projection_admin','connectmd_account_erasure','connectmd_backup'])
    )
    (SELECT * FROM expected EXCEPT SELECT * FROM actual)
    UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected)
  ) THEN RAISE EXCEPTION 'sequence ACL contract has missing or surplus authority'; END IF;
END
$acl_verify$;
\endif
