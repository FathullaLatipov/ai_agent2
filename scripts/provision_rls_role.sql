-- Optional: provision a non-owner application role so the RLS policies
-- (migration c7d8e9f0a1b2) actually bind. Run as a superuser, then point
-- AGENTOS_DATABASE_URL at this role and set `app.current_org` per transaction.
-- See docs/security.md §Authorization & tenancy for the honest status of this layer.

-- CHANGE THE PASSWORD before running.
CREATE ROLE agentos_app LOGIN PASSWORD 'change-me';

GRANT CONNECT ON DATABASE agentos TO agentos_app;
GRANT USAGE ON SCHEMA public TO agentos_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agentos_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agentos_app;
