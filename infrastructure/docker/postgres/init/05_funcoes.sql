-- functions


create or replace FUNCTION fn_data_fim_carencia(p_medicine_id UUID, p_data DATE)
    returns DATE as
$$
DECLARE
    v_dias INTEGER;
begin
    select meat_withdrawal_days into v_dias from medicine where id = p_medicine_id;

    if v_dias is null then
        raise EXCEPTION 'Medicamento % nao encontrado', p_medicine_id;
    end if;

    return p_data + v_dias;
end;
$$ language plpgsql;


create or replace FUNCTION fn_lote_apto_abate(p_lot_id UUID, p_data DATE default current_date)
    returns BOOLEAN as
$$
begin
    return not exists (select 1
                       from application
                       where lot_id = p_lot_id
                         and meat_withdrawal_end > p_data);
end;
$$ language plpgsql;


create or replace FUNCTION fn_saldo_medicine_batch(p_batch_id UUID)
    returns NUMERIC as
$$
DECLARE
    v_saldo NUMERIC(10, 3);
begin
    select COALESCE(SUM(case when type = 'E' then quantity else -quantity end), 0)
    into v_saldo
    from stock_movement
    where medicine_batch_id = p_batch_id;

    return v_saldo;
end;
$$ language plpgsql;


create or replace FUNCTION fn_tentativas_falhas(p_user_id UUID)
    returns INTEGER as
$$
DECLARE
    v_ultimo_sucesso TIMESTAMP;
    v_tentativas     INTEGER;
begin
    select MAX(happened_at)
    into v_ultimo_sucesso
    from access_audit
    where user_id = p_user_id
      and success;

    select COUNT(*)
    into v_tentativas
    from access_audit
    where user_id = p_user_id
      and not success
      and (v_ultimo_sucesso is null or happened_at > v_ultimo_sucesso);

    return v_tentativas;
end;
$$ language plpgsql;


create or replace FUNCTION fn_tentativas_falhas(p_login_tried VARCHAR)
    returns INTEGER as
$$
DECLARE
    v_ultimo_sucesso TIMESTAMP;
    v_tentativas     INTEGER;
begin
    select MAX(happened_at)
    into v_ultimo_sucesso
    from access_audit
    where login_tried = p_login_tried
      and success;

    select COUNT(*)
    into v_tentativas
    from access_audit
    where login_tried = p_login_tried
      and not success
      and (v_ultimo_sucesso is null or happened_at > v_ultimo_sucesso);

    return v_tentativas;
end;
$$ language plpgsql;


create or replace FUNCTION fn_resolve_categoria(p_texto TEXT)
    returns UUID as
$$
DECLARE
    v_id   UUID;
    v_norm TEXT := fn_norm_texto(p_texto);
begin
    if v_norm is not null then
        select id into v_id from therapeutic_category where name_norm = v_norm;
    end if;

    if v_id is null then
        select id into v_id from therapeutic_category where name = 'Não informado';
    end if;

    return v_id;
end;
$$ language plpgsql;

