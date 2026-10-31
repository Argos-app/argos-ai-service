--create database dbArgos;

create EXTENSION if not exists pgcrypto;
-- gerar o uuid aleatório
create EXTENSION if not exists unaccent;


create or replace FUNCTION fn_norm_texto(p_texto TEXT)
    returns TEXT as
$$
select NULLIF(TRIM(regexp_replace(lower(unaccent(COALESCE(p_texto, ''))), '\s+', ' ', 'g')), '');
$$ language sql immutable;

create or replace FUNCTION fn_em_carga()
    returns BOOLEAN as
$$
select COALESCE(NULLIF(current_setting('app.carga', true), ''), 'false')::BOOLEAN;
$$ language sql stable;


