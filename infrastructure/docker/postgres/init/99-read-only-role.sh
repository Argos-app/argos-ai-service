#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=reader_password="${POSTGRES_READER_PASSWORD:-argos_reader_local_only}" \
  --set=database_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE argos_reader LOGIN PASSWORD :'reader_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE :"database_name" TO argos_reader;
GRANT USAGE ON SCHEMA public TO argos_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO argos_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO argos_reader;
CREATE TABLE public.local_environment_ready (ready BOOLEAN PRIMARY KEY CHECK (ready));
INSERT INTO public.local_environment_ready (ready) VALUES (TRUE);
GRANT SELECT ON public.local_environment_ready TO argos_reader;
SQL
