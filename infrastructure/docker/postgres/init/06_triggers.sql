-- triggers
create or replace FUNCTION fn_atualiza_data()
    returns trigger as
$$
begin
    NEW.updated_at := current_timestamp;
    return NEW;
end;
$$ language plpgsql;

create trigger trg_atualiza_data_app_user
    BEFORE update
    on app_user
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_property
    BEFORE update
    on property
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_herd
    BEFORE update
    on herd
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_lot
    BEFORE update
    on lot
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_animal_exception
    BEFORE update
    on animal_exception
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_supplier
    BEFORE update
    on supplier
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_medicine
    BEFORE update
    on medicine
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_medicine_batch
    BEFORE update
    on medicine_batch
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_application
    BEFORE update
    on application
    for each row
execute FUNCTION fn_atualiza_data();
create trigger trg_atualiza_data_data_catalog
    BEFORE update
    on data_catalog
    for each row
execute FUNCTION fn_atualiza_data();

create or replace FUNCTION fn_valida_hierarquia_lote()
    returns trigger as
$$
DECLARE
    v_herd_pai UUID;
    v_ciclo    BOOLEAN;
begin
    if NEW.parent_lot_id is null then
        return NEW;
    end if;

    select herd_id
    into v_herd_pai
    from lot
    where id = NEW.parent_lot_id;

    if v_herd_pai is distinct from NEW.herd_id then
        raise EXCEPTION 'Lote pai deve pertencer ao mesmo rebanho';
    end if;

    with recursive ancestrais as (
        select id, parent_lot_id, ARRAY[id] as caminho
        from lot
        where id = NEW.parent_lot_id
        union all
        select l.id, l.parent_lot_id, a.caminho || l.id
        from lot l
                 join ancestrais a on l.id = a.parent_lot_id
        where not l.id = any(a.caminho)
    )
    select exists(select 1 from ancestrais where id = NEW.id)
    into v_ciclo;

    if v_ciclo then
        raise EXCEPTION 'Hierarquia de lotes nao pode conter ciclo';
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_valida_hierarquia_lote
    BEFORE insert or update of parent_lot_id, herd_id
    on lot
    for each row
execute FUNCTION fn_valida_hierarquia_lote();

create or replace FUNCTION fn_atualiza_contagem_rebanho()
    returns trigger as
$$
begin
    if TG_OP in ('UPDATE', 'DELETE') then
        update herd
        set head_count = (select COALESCE(SUM(head_count), 0)
                          from lot
                          where herd_id = OLD.herd_id
                            and active = true)
        where id = OLD.herd_id;
    end if;

    if TG_OP in ('INSERT', 'UPDATE') then
        update herd
        set head_count = (select COALESCE(SUM(head_count), 0)
                          from lot
                          where herd_id = NEW.herd_id
                            and active = true)
        where id = NEW.herd_id;
    end if;

    if TG_OP = 'DELETE' then
        return OLD;
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_atualiza_contagem_rebanho
    AFTER insert or update or delete
    on lot
    for each row
execute FUNCTION fn_atualiza_contagem_rebanho();

create or replace FUNCTION fn_valida_animal_exception()
    returns trigger as
$$
DECLARE
    v_parent_lot_id    UUID;
    v_is_exception_lot BOOLEAN;
    v_lot_active       BOOLEAN;
begin
    if fn_em_carga() then
        return NEW;
    end if;

    if TG_OP = 'UPDATE' and NEW.origin_lot_id is distinct from OLD.origin_lot_id then
        raise EXCEPTION 'Lote de origem do animal nao pode ser alterado';
    end if;

    if NEW.status = 'ATIVO' then
        select parent_lot_id, is_exception_lot, active
        into v_parent_lot_id, v_is_exception_lot, v_lot_active
        from lot
        where id = NEW.lot_id;

        if not COALESCE(v_lot_active, false)
            or not COALESCE(v_is_exception_lot, false)
            or v_parent_lot_id is distinct from NEW.origin_lot_id then
            raise EXCEPTION 'Animal ativo deve pertencer ao sublote de excecao do lote de origem';
        end if;
    elsif NEW.status = 'RECUPERADO' and NEW.lot_id is distinct from NEW.origin_lot_id then
        raise EXCEPTION 'Animal recuperado deve retornar ao lote de origem';
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_valida_animal_exception
    BEFORE insert or update of lot_id, origin_lot_id, status
    on animal_exception
    for each row
execute FUNCTION fn_valida_animal_exception();

create or replace FUNCTION fn_auditoria()
    returns trigger as
$$
DECLARE
    v_app_user UUID;
    v_ip       INET;
begin
    if fn_em_carga() then
        return COALESCE(NEW, OLD);
    end if;

    begin
        v_app_user := NULLIF(current_setting('app.usuario', true), '')::UUID;
        v_ip := NULLIF(current_setting('app.ip', true), '')::INET;
    EXCEPTION
        when OTHERS then
            v_app_user := null;
            v_ip := null;
    end;

    insert into change_audit (table_name, operation, record_id, old_data, new_data,
                              db_user, app_user_id, ip_address)
    values (TG_TABLE_NAME,
            TG_OP,
            COALESCE(NEW.id, OLD.id)::VARCHAR,
            case when TG_OP in ('UPDATE', 'DELETE') then row_to_json(OLD)::JSONB end,
            case when TG_OP in ('INSERT', 'UPDATE') then row_to_json(NEW)::JSONB end,
            CURRENT_USER,
            v_app_user,
            v_ip);

    return COALESCE(NEW, OLD);
end;
$$ language plpgsql;

create trigger trg_auditoria_application
    AFTER insert or update or delete
    on application
    for each row
execute FUNCTION fn_auditoria();

create trigger trg_auditoria_stock_movement
    AFTER insert or update or delete
    on stock_movement
    for each row
execute FUNCTION fn_auditoria();


create or replace FUNCTION fn_calcula_carencia_lote()
    returns trigger as
$$
DECLARE
    v_medicine_id UUID;
    v_head_count  INTEGER;
    v_batch_status VARCHAR(15);
    v_expiry_date  DATE;
begin
    if fn_em_carga() then
        return NEW;
    end if;

    select mb.medicine_id, mb.status, mb.expiry_date
    into v_medicine_id, v_batch_status, v_expiry_date
    from medicine_batch mb
             join medicine m on m.id = mb.medicine_id
    where mb.id = NEW.medicine_batch_id
      and m.active = true;

    if v_medicine_id is null
        or v_batch_status <> 'DISPONIVEL'
        or v_expiry_date < NEW.application_date then
        raise EXCEPTION 'Lote de medicamento indisponivel, inativo ou vencido';
    end if;

    select l.head_count
    into v_head_count
    from lot l
             join herd h on h.id = l.herd_id
             join property p on p.id = h.property_id
    where l.id = NEW.lot_id
      and l.active = true
      and h.active = true
      and p.active = true;

    if v_head_count is null then
        raise EXCEPTION 'Lote, rebanho ou propriedade inativo';
    end if;

    if not exists (select 1 from app_user where id = NEW.user_id and active = true) then
        raise EXCEPTION 'Usuario inativo';
    end if;

    if NEW.head_count_applied > v_head_count then
        raise EXCEPTION 'Quantidade aplicada maior que a quantidade de animais do lote';
    end if;

    NEW.meat_withdrawal_end := fn_data_fim_carencia(v_medicine_id, NEW.application_date);

    if NEW.head_count_applied is null then
        select head_count into NEW.head_count_applied from lot where id = NEW.lot_id;
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_calcula_carencia_lote
    BEFORE insert
    on application
    for each row
execute FUNCTION fn_calcula_carencia_lote();

create or replace FUNCTION fn_protege_aplicacao()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return COALESCE(NEW, OLD);
    end if;

    if TG_OP = 'DELETE' then
        raise EXCEPTION 'Aplicacoes usam soft delete e nao podem ser excluidas';
    end if;

    if NEW.lot_id is distinct from OLD.lot_id
        or NEW.medicine_batch_id is distinct from OLD.medicine_batch_id
        or NEW.user_id is distinct from OLD.user_id
        or NEW.application_date is distinct from OLD.application_date
        or NEW.application_time is distinct from OLD.application_time
        or NEW.dose is distinct from OLD.dose
        or NEW.unit is distinct from OLD.unit
        or NEW.route is distinct from OLD.route
        or NEW.head_count_applied is distinct from OLD.head_count_applied
        or NEW.meat_withdrawal_end is distinct from OLD.meat_withdrawal_end then
        raise EXCEPTION 'Campos historicos da aplicacao nao podem ser alterados';
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_protege_aplicacao
    BEFORE update or delete
    on application
    for each row
execute FUNCTION fn_protege_aplicacao();


create or replace FUNCTION fn_atualiza_apto_abate_lote()
    returns trigger as
$$
DECLARE
    v_maior DATE;
begin
    if fn_em_carga() then
        return NEW;
    end if;

    select MAX(meat_withdrawal_end)
    into v_maior
    from application
    where lot_id = NEW.lot_id;

    update lot
    set slaughter_release_date = v_maior,
        fit_for_slaughter      = (v_maior is null or v_maior <= current_date)
    where id = NEW.lot_id;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_atualiza_apto_abate_lote
    AFTER insert or update
    on application
    for each row
execute FUNCTION fn_atualiza_apto_abate_lote();


create or replace FUNCTION fn_calcula_carencia_excecao()
    returns trigger as
$$
DECLARE
    v_medicine_id UUID;
    v_data        DATE;
    v_lot_id      UUID;
begin
    if fn_em_carga() then
        return NEW;
    end if;

    select mb.medicine_id, ap.application_date, ap.lot_id
    into v_medicine_id, v_data, v_lot_id
    from application ap
             join medicine_batch mb on mb.id = ap.medicine_batch_id
    where ap.id = NEW.application_id;

    if not exists (
        select 1
        from animal_exception
        where id = NEW.animal_exception_id
          and lot_id = v_lot_id
          and status = 'ATIVO'
    ) then
        raise EXCEPTION 'Animal % nao pertence ao lote da aplicacao % ou nao esta ativo',
            NEW.animal_exception_id, NEW.application_id;
    end if;

    NEW.meat_withdrawal_end := fn_data_fim_carencia(v_medicine_id, v_data);
    NEW.released_for_slaughter := (NEW.meat_withdrawal_end <= current_date);

    update animal_exception
    set slaughter_release_date = GREATEST(COALESCE(slaughter_release_date, NEW.meat_withdrawal_end),
                                          NEW.meat_withdrawal_end),
        fit_for_slaughter      = (NEW.meat_withdrawal_end <= current_date)
    where id = NEW.animal_exception_id;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_calcula_carencia_excecao
    BEFORE insert
    on application_animal
    for each row
execute FUNCTION fn_calcula_carencia_excecao();

create or replace FUNCTION fn_protege_application_animal()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return COALESCE(NEW, OLD);
    end if;

    raise EXCEPTION 'Vinculos entre aplicacao e animal nao podem ser alterados ou excluidos';
end;
$$ language plpgsql;

create trigger trg_protege_application_animal
    BEFORE update or delete
    on application_animal
    for each row
execute FUNCTION fn_protege_application_animal();


create or replace FUNCTION fn_baixa_estoque()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return NEW;
    end if;

    perform 1
    from medicine_batch
    where id = NEW.medicine_batch_id
    for update;

    if fn_saldo_medicine_batch(NEW.medicine_batch_id) < NEW.dose * NEW.head_count_applied then
        raise EXCEPTION 'Saldo insuficiente no lote de medicamento %', NEW.medicine_batch_id;
    end if;

    insert into stock_movement (medicine_batch_id, application_id, user_id,
                                type, quantity, reason)
    values (NEW.medicine_batch_id, NEW.id, NEW.user_id,
            'S', NEW.dose * NEW.head_count_applied, 'APLICACAO');

    return NEW;
end;
$$ language plpgsql;

create trigger trg_baixa_estoque
    AFTER insert
    on application
    for each row
execute FUNCTION fn_baixa_estoque();

create or replace FUNCTION fn_protege_movimento_estoque()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return COALESCE(NEW, OLD);
    end if;

    raise EXCEPTION 'Movimentos de estoque nao podem ser alterados ou excluidos';
end;
$$ language plpgsql;

create trigger trg_protege_movimento_estoque
    BEFORE update or delete
    on stock_movement
    for each row
execute FUNCTION fn_protege_movimento_estoque();

create or replace FUNCTION fn_valida_saldo_movimento()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return NEW;
    end if;

    perform 1
    from medicine_batch
    where id = NEW.medicine_batch_id
    for update;

    if NEW.type = 'S'
        and fn_saldo_medicine_batch(NEW.medicine_batch_id) < NEW.quantity then
        raise EXCEPTION 'Saldo insuficiente no lote de medicamento %', NEW.medicine_batch_id;
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_valida_saldo_movimento
    BEFORE insert
    on stock_movement
    for each row
execute FUNCTION fn_valida_saldo_movimento();


create or replace FUNCTION fn_gera_alerta()
    returns trigger as
$$
DECLARE
    v_codigo VARCHAR(20);
begin
    if fn_em_carga()
        or NEW.meat_withdrawal_end is null
        or NEW.meat_withdrawal_end <= current_date then
        return NEW;
    end if;

    select lot_code into v_codigo from lot where id = NEW.lot_id;

    insert into alert (user_id, lot_id, type, severity, title, message)
    values (NEW.user_id,
            NEW.lot_id,
            'CARENCIA',
            'MEDIA',
            'Lote em carencia: ' || v_codigo,
            'O lote ' || v_codigo || ' fica em carencia ate ' ||
            TO_CHAR(NEW.meat_withdrawal_end, 'DD/MM/YYYY') || '.');

    return NEW;
end;
$$ language plpgsql;

create trigger trg_gera_alerta
    AFTER insert
    on application
    for each row
execute FUNCTION fn_gera_alerta();


create or replace FUNCTION fn_atualiza_contagem()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return COALESCE(NEW, OLD);
    end if;

    if TG_OP = 'INSERT' and NEW.status = 'ATIVO' then
        update lot
        set head_count = head_count - 1
        where id = NEW.origin_lot_id
          and head_count > 0;

        if not FOUND then
            raise EXCEPTION 'Lote de origem % sem animais disponiveis', NEW.origin_lot_id;
        end if;

        update lot set head_count = head_count + 1 where id = NEW.lot_id;
    ELSIF TG_OP = 'UPDATE' then
        if NEW.lot_id is distinct from OLD.lot_id then
            if not (OLD.status = 'ATIVO'
                and NEW.status = 'RECUPERADO'
                and NEW.lot_id = OLD.origin_lot_id) then
                raise EXCEPTION 'Mudanca de lote deve ser feita pelo retorno da excecao';
            end if;

            update lot
            set head_count = head_count - 1
            where id = OLD.lot_id
              and head_count > 0;

            if not FOUND then
                raise EXCEPTION 'Lote atual % sem animais disponiveis', OLD.lot_id;
            end if;

            update lot set head_count = head_count + 1 where id = NEW.lot_id;
        elsif OLD.status = 'ATIVO'
            and NEW.status in ('VENDIDO', 'ABATIDO', 'MORTO') then
            update lot
            set head_count = head_count - 1
            where id = OLD.lot_id
              and head_count > 0;

            if not FOUND then
                raise EXCEPTION 'Lote atual % sem animais disponiveis', OLD.lot_id;
            end if;

            update animal_lot_history
            set exit_date = current_date
            where animal_exception_id = NEW.id
              and exit_date is null;
        elsif OLD.status <> 'ATIVO' and NEW.status = 'ATIVO' then
            raise EXCEPTION 'Animal encerrado nao pode voltar ao status ATIVO';
        elsif OLD.status = 'ATIVO' and NEW.status = 'RECUPERADO' then
            raise EXCEPTION 'Animal recuperado deve retornar ao lote de origem';
        end if;
    end if;

    if TG_OP = 'UPDATE'
        and OLD.status = 'ATIVO'
        and NEW.status in ('RECUPERADO', 'VENDIDO', 'ABATIDO', 'MORTO') then
        update lot l
        set active = false,
            close_date = current_date
        where l.id = OLD.lot_id
          and l.is_exception_lot = true
          and not exists (
              select 1
              from animal_exception ae
              where ae.lot_id = l.id
                and ae.status = 'ATIVO'
          );
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_atualiza_contagem
    AFTER insert or update
    on animal_exception
    for each row
execute FUNCTION fn_atualiza_contagem();

create or replace FUNCTION fn_protege_exclusao_animal_exception()
    returns trigger as
$$
begin
    if fn_em_carga() then
        return OLD;
    end if;

    raise EXCEPTION 'Animais de excecao usam status e nao podem ser excluidos';
end;
$$ language plpgsql;

create trigger trg_protege_exclusao_animal_exception
    BEFORE delete
    on animal_exception
    for each row
execute FUNCTION fn_protege_exclusao_animal_exception();


create or replace FUNCTION fn_registra_acesso_diario()
    returns trigger as
$$
begin
    if NEW.success and NEW.user_id is not null then
        insert into daily_access (user_id, date, source, session_count, first_login_time)
        values (NEW.user_id, NEW.happened_at::DATE, NEW.source, 1, NEW.happened_at)
        on CONFLICT (user_id, date, source)
            do update set session_count = daily_access.session_count + 1;
    end if;

    return NEW;
end;
$$ language plpgsql;

create trigger trg_registra_acesso_diario
    AFTER insert
    on access_audit
    for each row
execute FUNCTION fn_registra_acesso_diario();

