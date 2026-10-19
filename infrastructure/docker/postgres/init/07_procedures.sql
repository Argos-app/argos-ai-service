-- procedures
create or replace procedure sp_registrar_aplicacao(
    p_lot_id UUID,
    p_medicine_batch_id UUID,
    p_user_id UUID,
    p_dose NUMERIC,
    p_unit VARCHAR,
    p_route VARCHAR,
    p_goal VARCHAR default null
) as
$$
DECLARE
    v_cabecas INTEGER;
    v_saldo   NUMERIC(10, 3);
begin
    select head_count into v_cabecas from lot where id = p_lot_id;

    if v_cabecas is null or v_cabecas = 0 then
        raise EXCEPTION 'Lote % nao tem animal', p_lot_id;
    end if;

    v_saldo := fn_saldo_medicine_batch(p_medicine_batch_id);

    if v_saldo < p_dose * v_cabecas then
        raise EXCEPTION 'Saldo insuficiente no frasco %: tem %, precisa de %',
            p_medicine_batch_id, v_saldo, p_dose * v_cabecas;
    end if;

    insert into application (lot_id, medicine_batch_id, user_id, application_date,
                             application_time, dose, unit, route, head_count_applied, goal)
    values (p_lot_id, p_medicine_batch_id, p_user_id, current_date,
            current_time, p_dose, p_unit, p_route, v_cabecas, p_goal);

    commit;
EXCEPTION
    when OTHERS then
        rollback;
        raise;
end;
$$ language plpgsql;

create or replace procedure sp_registrar_aplicacao_animal(
    p_animal_exception_id UUID,
    p_medicine_batch_id UUID,
    p_user_id UUID,
    p_dose NUMERIC,
    p_unit VARCHAR,
    p_route VARCHAR,
    p_goal VARCHAR default null
)
as
$$
DECLARE
    v_lot_id       UUID;
    v_peso         NUMERIC(7,2);
    v_saldo        NUMERIC(10,3);
    v_application  UUID;
    v_carencia     DATE;
begin

    -- localizar o animal e o lote onde ele ta
    select lot_id, current_weight
    into v_lot_id, v_peso
    from animal_exception
    where id = p_animal_exception_id
      and status = 'ATIVO';

    if v_lot_id is null then
        raise EXCEPTION
            'Animal de excecao % nao encontrado ou nao esta ativo',
            p_animal_exception_id;
    end if;



    v_saldo := fn_saldo_medicine_batch(p_medicine_batch_id);

    if v_saldo < p_dose then
        raise EXCEPTION
            'Saldo insuficiente no frasco %: tem %, precisa de %',
            p_medicine_batch_id,
            v_saldo,
            p_dose;
    end if;


    -- calcular fim da carencia
    select fn_data_fim_carencia(medicine_id, current_date)
    into v_carencia
    from medicine_batch
    where id = p_medicine_batch_id;

    if v_carencia is null then
        raise EXCEPTION
            'Lote de medicamento % nao encontrado',
            p_medicine_batch_id;
    end if;


    -- registra a aplicação principal
    insert into application (
        lot_id,
        medicine_batch_id,
        user_id,
        application_date,
        application_time,
        dose,
        unit,
        route,
        head_count_applied,
        meat_withdrawal_end,
        goal
    )
    values (
               v_lot_id,
               p_medicine_batch_id,
               p_user_id,
               current_date,
               current_time,
               p_dose,
               p_unit,
               p_route,
               1,
               v_carencia,
               p_goal
           )
    returning id into v_application;


    -- associar especificamente ao animal
    insert into application_animal (
        application_id,
        animal_exception_id,
        dose_given,
        weight_at_time,
        meat_withdrawal_end,
        released_for_slaughter
    )
    values (
               v_application,
               p_animal_exception_id,
               p_dose,
               v_peso,
               v_carencia,
               v_carencia <= current_date
           );


    commit;

EXCEPTION
    when OTHERS then
        rollback;
        raise;
end;
$$ language plpgsql;

create or replace procedure sp_marcar_excecao(
    p_lot_id UUID,
    p_ear_tag VARCHAR,
    p_motivo VARCHAR,
    p_peso NUMERIC default null
)
as
$$
DECLARE
    v_sublote   UUID;
    v_herd      UUID;
    v_codigo    VARCHAR(20);
    v_categoria VARCHAR(30);
    v_seq       INTEGER;
    v_animal    UUID;
begin

    select herd_id,
           lot_code,
           category
    into v_herd,
        v_codigo,
        v_categoria
    from lot
    where id = p_lot_id;

    if v_herd is null then
        raise EXCEPTION 'Lote % nao encontrado', p_lot_id;
    end if;


    select id
    into v_sublote
    from lot
    where parent_lot_id = p_lot_id
      and is_exception_lot = true
      and active = true
      and fn_norm_texto(exception_reason) = fn_norm_texto(p_motivo);


    if v_sublote is null then

        select COUNT(*) + 1
        into v_seq
        from lot
        where parent_lot_id = p_lot_id
          and is_exception_lot = true;


        insert into lot (
            herd_id,
            parent_lot_id,
            lot_code,
            category,
            is_exception_lot,
            exception_reason
        )
        values (
                   v_herd,
                   p_lot_id,
                   left(v_codigo, 13) || '-EXC' || v_seq,
                   v_categoria,
                   true,
                   p_motivo
               )
        returning id into v_sublote;

    end if;


    insert into animal_exception (
        lot_id,
        origin_lot_id,
        ear_tag,
        category,
        current_weight,
        exception_reason
    )
    values (
               v_sublote,
               p_lot_id,
               p_ear_tag,
               'BOI',
               p_peso,
               p_motivo
           )
    returning id into v_animal;


    insert into animal_lot_history (
        animal_exception_id,
        lot_id,
        reason
    )
    values (
               v_animal,
               v_sublote,
               'EXCECAO'
           );


    commit;

EXCEPTION
    when OTHERS then
        rollback;
        raise;
end;
$$ language plpgsql;


create or replace procedure sp_retornar_excecao(p_animal_id UUID)
as
$$
DECLARE
    v_origem   UUID;
    v_sublote  UUID;
    v_carencia DATE;
    v_restante INTEGER;
begin
    select ae.origin_lot_id,
           ae.lot_id,
           GREATEST(COALESCE(ae.slaughter_release_date, current_date - 1),
                    COALESCE((select MAX(ap.meat_withdrawal_end)
                              from application ap
                              where ap.lot_id = ae.lot_id), current_date - 1))
    into v_origem, v_sublote, v_carencia
    from animal_exception ae
    where ae.id = p_animal_id
      and ae.status = 'ATIVO';

    if v_origem is null then
        raise EXCEPTION 'Animal % nao encontrado ou nao esta em excecao ativa', p_animal_id;
    end if;

    if v_carencia > current_date then
        raise EXCEPTION 'Animal % ainda em carencia ate %', p_animal_id, v_carencia;
    end if;

    update animal_lot_history
    set exit_date = current_date
    where animal_exception_id = p_animal_id
      and lot_id = v_sublote
      and exit_date is null;

    update animal_exception
    set lot_id             = v_origem,
        status             = 'RECUPERADO',
        exception_end_date = current_date,
        fit_for_slaughter  = true
    where id = p_animal_id;

    insert into animal_lot_history (animal_exception_id, lot_id, reason)
    values (p_animal_id, v_origem, 'RETORNO_EXCECAO');

    select head_count into v_restante from lot where id = v_sublote;

    if COALESCE(v_restante, 0) <= 0 then
        update lot
        set close_date = current_date,
            active     = false
        where id = v_sublote;
    end if;

    commit;
EXCEPTION
    when OTHERS then
        rollback;
        raise;
end;
$$ language plpgsql;


create or replace FUNCTION fn_prioridade_carga(p_tabela VARCHAR)
    returns INTEGER as
$$
select case p_tabela
           when 'app_user' then 1
           when 'property' then 2
           when 'herd' then 3
           when 'lot' then 4
           when 'animal_exception' then 5
           when 'supplier' then 6
           when 'medicine' then 7
           when 'medicine_batch' then 8
           when 'application' then 9
           when 'stock_movement' then 10
           else 99
           end;
$$ language sql immutable;


create or replace FUNCTION fn_so_digitos(p_texto TEXT)
    returns TEXT as
$$
select NULLIF(regexp_replace(COALESCE(p_texto, ''), '[^0-9]', '', 'g'), '');
$$ language sql immutable;


create or replace FUNCTION fn_de_para(p_tabela VARCHAR, p_source_id TEXT)
    returns UUID as
$$
DECLARE
    v_id UUID;
begin
    if p_source_id is null then
        return null;
    end if;

    select target_id
    into v_id
    from load_mapping
    where table_name = p_tabela
      and source_id = p_source_id;

    if v_id is null then
        raise EXCEPTION 'Dependencia nao carregada: % de origem %', p_tabela, p_source_id;
    end if;

    return v_id;
end;
$$ language plpgsql;


create or replace FUNCTION fn_de_para_enum(p_texto TEXT, p_valores TEXT[], p_default TEXT)
    returns TEXT as
$$
DECLARE
    v_norm TEXT := fn_norm_texto(p_texto);
    v_item TEXT;
begin
    if v_norm is null then
        return p_default;
    end if;

    foreach v_item in array p_valores
        loop
            if fn_norm_texto(v_item) = v_norm then
                return v_item;
            end if;
        end loop;

    return p_default;
end;
$$ language plpgsql;


create or replace FUNCTION fn_unidade_medida(p_texto TEXT)
    returns TEXT as
$$
DECLARE
    v_norm TEXT := fn_norm_texto(p_texto);
begin
    return case
               when v_norm in ('ml', 'mililitro', 'mililitros') then 'ml'
               when v_norm in ('mg', 'miligrama', 'miligramas') then 'mg'
               when v_norm in ('g', 'grama', 'gramas') then 'g'
               when v_norm in ('ui', 'unidade internacional') then 'UI'
               when v_norm in ('dose', 'doses') then 'dose'
               else null
               end;
end;
$$ language plpgsql;

create or replace procedure sp_carga_rpa(p_load_run_id UUID)
as
$$
DECLARE
    r            RECORD;
    v_json       JSONB;
    v_tabela     VARCHAR(60);
    v_chave      VARCHAR(120);
    v_source     TEXT;
    v_target     UUID;
    v_novo       BOOLEAN;
    v_rpa        UUID;
    v_pai        UUID;
    v_aux        UUID;
    v_texto      TEXT;
    v_status     VARCHAR(15);
    v_data       DATE;
    v_lidos      INTEGER := 0;
    v_inseridos  INTEGER := 0;
    v_atualizado INTEGER := 0;
    v_rejeitados INTEGER := 0;
begin
    perform set_config('app.carga', 'true', true);

    select id
    into v_rpa
    from app_user
    where cpf = '00000000000';

    for r in
        select *
        from load_staging
        where load_run_id = p_load_run_id
          and processed = false
        order by fn_prioridade_carga(
                         ((source_columns)::JSONB) ->> 'tabela'
                 ),
                 extract_date
        loop

            v_lidos := v_lidos + 1;
            v_novo := false;
            v_target := null;

            begin
                v_json := r.source_columns::JSONB;
                v_tabela := v_json ->> 'tabela';
                v_source := v_json ->> 'source_id';
                v_chave := v_source;


                --app user

                if v_tabela = 'app_user' then

                    v_chave := fn_so_digitos(v_json ->> 'cpf');

                    if v_chave is null then
                        raise EXCEPTION 'Usuario sem CPF valido';
                    end if;

                    select id
                    into v_target
                    from app_user
                    where cpf = v_chave;

                    if v_target is null then

                        select p.id
                        into v_pai
                        from permission p
                        where p.code = 'APLICADOR';

                        insert into app_user (
                            permission_id,
                            name,
                            cpf,
                            email,
                            active,
                            source_id
                        )
                        values (
                                   v_pai,
                                   v_json ->> 'name',
                                   v_chave,
                                   lower(TRIM(v_json ->> 'email')),
                                   COALESCE(
                                           (v_json ->> 'active')::BOOLEAN,
                                           true
                                   ),
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update app_user
                        set name = v_json ->> 'name',
                            email = lower(TRIM(v_json ->> 'email')),
                            active = COALESCE(
                                    (v_json ->> 'active')::BOOLEAN,
                                    true
                                     )
                        where id = v_target;

                    end if;



                    -- property


                ELSIF v_tabela = 'property' then

                    select id
                    into v_target
                    from property
                    where source_id = v_source::UUID;

                    v_pai := fn_de_para(
                            'app_user',
                            v_json ->> 'source_user_id'
                             );

                    if v_target is null then

                        insert into property (
                            user_id,
                            name,
                            cnpj,
                            street,
                            number,
                            complement,
                            zip_code,
                            city,
                            state,
                            area_hectares,
                            phone,
                            active,
                            source_id
                        )
                        values (
                                   v_pai,
                                   v_json ->> 'name',
                                   fn_so_digitos(v_json ->> 'cnpj'),
                                   left(v_json ->> 'street', 150),
                                   COALESCE(
                                           left(v_json ->> 'number', 10),
                                           'S/N'
                                   ),
                                   left(v_json ->> 'complement', 100),
                                   fn_so_digitos(v_json ->> 'zip_code'),
                                   TRIM(v_json ->> 'city'),
                                   UPPER(TRIM(v_json ->> 'state')),
                                   (v_json ->> 'area_hectares')::NUMERIC,
                                   v_json ->> 'phone',
                                   COALESCE(
                                           (v_json ->> 'active')::BOOLEAN,
                                           true
                                   ),
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update property
                        set user_id = v_pai,
                            name = v_json ->> 'name',
                            cnpj = fn_so_digitos(v_json ->> 'cnpj'),
                            street = left(v_json ->> 'street', 150),
                            number = COALESCE(
                                    left(v_json ->> 'number', 10),
                                    'S/N'
                                     ),
                            complement = left(
                                    v_json ->> 'complement',
                                    100
                                         ),
                            zip_code = fn_so_digitos(
                                    v_json ->> 'zip_code'
                                       ),
                            city = TRIM(v_json ->> 'city'),
                            state = UPPER(
                                    TRIM(v_json ->> 'state')
                                    ),
                            area_hectares =
                                (v_json ->> 'area_hectares')::NUMERIC,
                            phone = v_json ->> 'phone',
                            active = COALESCE(
                                    (v_json ->> 'active')::BOOLEAN,
                                    true
                                     )
                        where id = v_target;

                    end if;

                    insert into user_property (
                        user_id,
                        property_id
                    )
                    values (
                               v_pai,
                               v_target
                           )
                    on CONFLICT (user_id, property_id)
                        do NOTHING;



                    -- herd


                ELSIF v_tabela = 'herd' then

                    select id
                    into v_target
                    from herd
                    where source_id = v_source::UUID;

                    v_pai := fn_de_para(
                            'property',
                            v_json ->> 'source_property_id'
                             );

                    if v_target is null then

                        insert into herd (
                            property_id,
                            herd_name,
                            breed,
                            head_count,
                            active,
                            source_id
                        )
                        values (
                                   v_pai,
                                   v_json ->> 'herd_name',
                                   COALESCE(
                                           NULLIF(
                                                   TRIM(v_json ->> 'breed'),
                                                   ''
                                           ),
                                           'NAO INFORMADA'
                                   ),
                                   COALESCE(
                                           (v_json ->> 'head_count')::INTEGER,
                                           0
                                   ),
                                   COALESCE(
                                           (v_json ->> 'active')::BOOLEAN,
                                           true
                                   ),
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update herd
                        set herd_name = v_json ->> 'herd_name',
                            breed = COALESCE(
                                    NULLIF(
                                            TRIM(v_json ->> 'breed'),
                                            ''
                                    ),
                                    'NAO INFORMADA'
                                    ),
                            head_count = COALESCE(
                                    (v_json ->> 'head_count')::INTEGER,
                                    head_count
                                         ),
                            active = COALESCE(
                                    (v_json ->> 'active')::BOOLEAN,
                                    true
                                     )
                        where id = v_target;

                    end if;



                    -- lot


                ELSIF v_tabela = 'lot' then

                    select id
                    into v_target
                    from lot
                    where source_id = v_source::UUID;

                    v_pai := fn_de_para(
                            'herd',
                            v_json ->> 'source_herd_id'
                             );

                    v_texto := fn_de_para_enum(
                            v_json ->> 'category',
                            ARRAY[
                                'ENGORDA',
                                'CRIA',
                                'RECRIA'
                                ],
                            'ENGORDA'
                               );

                    if v_target is null then

                        insert into lot (
                            herd_id,
                            lot_code,
                            category,
                            head_count,
                            open_date,
                            close_date,
                            active,
                            source_id
                        )
                        values (
                                   v_pai,
                                   left(
                                           v_json ->> 'lot_code',
                                           20
                                   ),
                                   v_texto,
                                   COALESCE(
                                           (v_json ->> 'head_count')::INTEGER,
                                           0
                                   ),
                                   COALESCE(
                                           (v_json ->> 'open_date')::DATE,
                                           current_date
                                   ),
                                   (v_json ->> 'close_date')::DATE,
                                   COALESCE(
                                           (v_json ->> 'active')::BOOLEAN,
                                           true
                                   ),
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update lot
                        set lot_code = left(
                                v_json ->> 'lot_code',
                                20
                                       ),
                            category = v_texto,
                            head_count = COALESCE(
                                    (v_json ->> 'head_count')::INTEGER,
                                    head_count
                                         ),
                            close_date =
                                (v_json ->> 'close_date')::DATE,
                            active = COALESCE(
                                    (v_json ->> 'active')::BOOLEAN,
                                    true
                                     )
                        where id = v_target;

                    end if;



                    -- animal exception



                ELSIF v_tabela = 'animal_exception' then

                    select id
                    into v_target
                    from animal_exception
                    where source_id = v_source::UUID;

                    v_pai := fn_de_para(
                            'lot',
                            COALESCE(
                                    v_json ->> 'source_origin_lot_id',
                                    v_json ->> 'source_lot_id'
                            )
                             );

                    v_texto := COALESCE(
                            NULLIF(
                                    TRIM(
                                            v_json ->> 'exception_reason'
                                    ),
                                    ''
                            ),
                            'NAO INFORMADO'
                               );

                    v_status := fn_de_para_enum(
                            v_json ->> 'status',
                            ARRAY[
                                'ATIVO',
                                'VENDIDO',
                                'ABATIDO',
                                'MORTO',
                                'RECUPERADO'
                                ],
                            'ATIVO'
                                );

                    v_data := COALESCE(
                            (v_json ->> 'exception_date')::DATE,
                            (v_json ->> 'updated_at')::DATE,
                            current_date
                              );

                    select id
                    into v_aux
                    from lot
                    where parent_lot_id = v_pai
                      and is_exception_lot = true
                      and (v_status <> 'ATIVO' or active = true)
                      and fn_norm_texto(exception_reason)
                        =
                          fn_norm_texto(v_texto)
                    order by active desc, created_at desc
                    limit 1;

                    if v_aux is null then

                        insert into lot (
                            herd_id,
                            parent_lot_id,
                            lot_code,
                            category,
                            is_exception_lot,
                            exception_reason,
                            open_date
                        )
                        select
                            l.herd_id,
                            l.id,
                            left(l.lot_code, 13)
                                || '-EXC'
                                ||
                            (
                                select COUNT(*) + 1
                                from lot x
                                where x.parent_lot_id = l.id
                                  and x.is_exception_lot = true
                            ),
                            l.category,
                            true,
                            v_texto,
                            v_data
                        from lot l
                        where l.id = v_pai
                        returning id into v_aux;

                    end if;

                    if v_target is null then

                        insert into animal_exception (
                            lot_id,
                            origin_lot_id,
                            ear_tag,
                            category,
                            birth_date,
                            current_weight,
                            exception_date,
                            exception_end_date,
                            exception_reason,
                            status,
                            notes,
                            source_id
                        )
                        values (
                                   case
                                       when v_status = 'RECUPERADO' then v_pai
                                       else v_aux
                                   end,
                                   v_pai,
                                   v_json ->> 'ear_tag',
                                   fn_de_para_enum(
                                           v_json ->> 'category',
                                           ARRAY[
                                               'BEZERRO',
                                               'GARROTE',
                                               'BOI',
                                               'TOURO'
                                               ],
                                           'BOI'
                                   ),
                                   (v_json ->> 'birth_date')::DATE,
                                   (v_json ->> 'current_weight')::NUMERIC,
                                   v_data,
                                   (v_json ->> 'exception_end_date')::DATE,
                                   left(v_texto, 255),
                                   v_status,
                                   v_json ->> 'notes',
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update animal_exception
                        set lot_id =
                                case
                                    when v_status = 'RECUPERADO' then v_pai
                                    else v_aux
                                end,
                            origin_lot_id = v_pai,
                            current_weight =
                                (v_json ->> 'current_weight')::NUMERIC,
                            exception_date = v_data,
                            exception_end_date =
                                (v_json ->> 'exception_end_date')::DATE,
                            status = v_status,
                            notes = v_json ->> 'notes'
                        where id = v_target;

                    end if;

                    update animal_lot_history
                    set entry_date = v_data,
                        exit_date =
                            case
                                when v_status = 'ATIVO' then null
                                else COALESCE(
                                        (v_json ->> 'exception_end_date')::DATE,
                                        v_data
                                     )
                            end
                    where animal_exception_id = v_target
                      and lot_id = v_aux
                      and reason = 'EXCECAO';

                    if not FOUND then

                        insert into animal_lot_history (
                            animal_exception_id,
                            lot_id,
                            entry_date,
                            exit_date,
                            reason
                        )
                        values (
                                   v_target,
                                   v_aux,
                                   v_data,
                                   case
                                       when v_status = 'ATIVO' then null
                                       else COALESCE(
                                               (v_json ->> 'exception_end_date')::DATE,
                                               v_data
                                            )
                                   end,
                                   'EXCECAO'
                               );

                    end if;

                    if v_status = 'RECUPERADO' then

                        update animal_lot_history
                        set entry_date = COALESCE(
                                (v_json ->> 'exception_end_date')::DATE,
                                v_data
                                         ),
                            exit_date = null
                        where animal_exception_id = v_target
                          and lot_id = v_pai
                          and reason = 'RETORNO_EXCECAO';

                        if not FOUND then

                            insert into animal_lot_history (
                                animal_exception_id,
                                lot_id,
                                entry_date,
                                reason
                            )
                            values (
                                       v_target,
                                       v_pai,
                                       COALESCE(
                                               (v_json ->> 'exception_end_date')::DATE,
                                               v_data
                                       ),
                                       'RETORNO_EXCECAO'
                                   );

                        end if;

                    end if;



                    -- supplier


                ELSIF v_tabela = 'supplier' then

                    v_chave := fn_so_digitos(
                            v_json ->> 'cnpj'
                               );

                    if v_chave is null then
                        raise EXCEPTION
                            'Fornecedor sem CNPJ valido';
                    end if;

                    select id
                    into v_target
                    from supplier
                    where cnpj = v_chave;

                    if v_target is null then

                        insert into supplier (
                            company_name,
                            cnpj,
                            email,
                            phone,
                            address,
                            active,
                            source_id
                        )
                        values (
                                   v_json ->> 'company_name',
                                   v_chave,
                                   v_json ->> 'email',
                                   v_json ->> 'phone',
                                   left(
                                           v_json ->> 'address',
                                           255
                                   ),
                                   COALESCE(
                                           (v_json ->> 'active')::BOOLEAN,
                                           true
                                   ),
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update supplier
                        set company_name =
                                v_json ->> 'company_name',
                            email =
                                v_json ->> 'email',
                            phone =
                                v_json ->> 'phone',
                            address =
                                left(
                                        v_json ->> 'address',
                                        255
                                ),
                            active =
                                COALESCE(
                                        (v_json ->> 'active')::BOOLEAN,
                                        true
                                )
                        where id = v_target;

                    end if;



                    -- medicine


                ELSIF v_tabela = 'medicine' then

                    select id
                    into v_target
                    from medicine
                    where source_id = v_source::UUID;

                    v_pai := fn_de_para(
                            'supplier',
                            v_json ->> 'source_supplier_id'
                             );

                    v_aux := fn_resolve_categoria(
                            v_json ->> 'therapeutic_category'
                             );

                    v_texto := fn_unidade_medida(
                            v_json ->> 'unit'
                               );

                    if v_texto is null then
                        raise EXCEPTION
                            'Unidade de medida nao reconhecida: %',
                            v_json ->> 'unit';
                    end if;

                    if v_target is null then

                        insert into medicine (
                            supplier_id,
                            therapeutic_category_id,
                            brand_name,
                            active_ingredient,
                            mapa_registration,
                            indication,
                            description,
                            needs_prescription,
                            meat_withdrawal_days,
                            dose,
                            unit,
                            active,
                            source_id
                        )
                        values (
                                   v_pai,
                                   v_aux,
                                   v_json ->> 'brand_name',
                                   v_json ->> 'active_ingredient',
                                   v_json ->> 'mapa_registration',
                                   COALESCE(
                                           v_json ->> 'indication',
                                           'NAO INFORMADA'
                                   ),
                                   v_json ->> 'description',
                                   COALESCE(
                                           (v_json ->> 'needs_prescription')::BOOLEAN,
                                           false
                                   ),
                                   COALESCE(
                                           (v_json ->> 'meat_withdrawal_days')::INTEGER,
                                           0
                                   ),
                                   (v_json ->> 'dose')::NUMERIC,
                                   v_texto,
                                   COALESCE(
                                           (v_json ->> 'active')::BOOLEAN,
                                           true
                                   ),
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                    else

                        update medicine
                        set supplier_id = v_pai,
                            therapeutic_category_id = v_aux,
                            brand_name =
                                v_json ->> 'brand_name',
                            active_ingredient =
                                v_json ->> 'active_ingredient',
                            mapa_registration =
                                v_json ->> 'mapa_registration',
                            indication =
                                COALESCE(
                                        v_json ->> 'indication',
                                        indication
                                ),
                            description =
                                v_json ->> 'description',
                            needs_prescription =
                                COALESCE(
                                        (v_json ->> 'needs_prescription')::BOOLEAN,
                                        false
                                ),
                            meat_withdrawal_days =
                                COALESCE(
                                        (v_json ->> 'meat_withdrawal_days')::INTEGER,
                                        0
                                ),
                            dose =
                                (v_json ->> 'dose')::NUMERIC,
                            unit = v_texto,
                            active =
                                COALESCE(
                                        (v_json ->> 'active')::BOOLEAN,
                                        true
                                )
                        where id = v_target;

                    end if;



                    -- medicine batch

                ELSIF v_tabela = 'medicine_batch' then

                    select id
                    into v_target
                    from medicine_batch
                    where source_id = v_source::UUID;

                    v_pai := fn_de_para(
                            'medicine',
                            v_json ->> 'source_medicine_id'
                             );

                    if (v_json ->> 'expiry_date') is null then
                        raise EXCEPTION
                            'Lote de medicamento sem data de validade';
                    end if;

                    if v_target is null then

                        insert into medicine_batch (
                            medicine_id,
                            batch_number,
                            manufacture_date,
                            expiry_date,
                            delivery_date,
                            initial_quantity,
                            min_stock,
                            max_stock,
                            storage_place,
                            status,
                            source_id
                        )
                        values (
                                   v_pai,
                                   left(
                                           v_json ->> 'batch_number',
                                           30
                                   ),
                                   COALESCE(
                                           (v_json ->> 'manufacture_date')::DATE,
                                           (v_json ->> 'expiry_date')::DATE - 1
                                   ),
                                   (v_json ->> 'expiry_date')::DATE,
                                   COALESCE(
                                           (v_json ->> 'delivery_date')::DATE,
                                           current_date
                                   ),
                                   COALESCE(
                                           (v_json ->> 'initial_quantity')::NUMERIC,
                                           0.001
                                   ),
                                   (v_json ->> 'min_stock')::NUMERIC,
                                   (v_json ->> 'max_stock')::NUMERIC,
                                   v_json ->> 'storage_place',
                                   case
                                       when
                                           (v_json ->> 'expiry_date')::DATE
                                               < current_date
                                           then 'VENCIDO'
                                       else 'DISPONIVEL'
                                       end,
                                   v_source::UUID
                               )
                        returning id into v_target;

                        v_novo := true;

                        if COALESCE(
                                   (v_json ->> 'available_quantity')::NUMERIC,
                                   0
                           ) > 0 then

                            insert into stock_movement (
                                medicine_batch_id,
                                user_id,
                                type,
                                quantity,
                                reason,
                                source_id
                            )
                            values (
                                       v_target,
                                       v_rpa,
                                       'E',
                                       (v_json ->> 'available_quantity')::NUMERIC,
                                       'CARGA_INICIAL',
                                       v_source::UUID
                                   )
                            on CONFLICT do NOTHING;

                        end if;

                    else

                        update medicine_batch
                        set min_stock =
                                (v_json ->> 'min_stock')::NUMERIC,
                            max_stock =
                                (v_json ->> 'max_stock')::NUMERIC,
                            storage_place =
                                v_json ->> 'storage_place',
                            status =
                                case
                                    when
                                        (v_json ->> 'expiry_date')::DATE
                                            < current_date
                                        then 'VENCIDO'
                                    else status
                                    end
                        where id = v_target;

                    end if;


                    -- application


                ELSIF v_tabela = 'application' then

                    select id
                    into v_target
                    from application
                    where source_id = v_source::UUID;

                    if v_target is null then

                        v_pai := fn_de_para(
                                'lot',
                                v_json ->> 'source_lot_id'
                                 );

                        v_aux := fn_de_para(
                                'medicine_batch',
                                v_json ->> 'source_batch_id'
                                 );

                        v_texto := fn_unidade_medida(
                                v_json ->> 'unit'
                                   );

                        if v_texto is null then
                            raise EXCEPTION
                                'Unidade de medida nao reconhecida: %',
                                v_json ->> 'unit';
                        end if;

                        v_data := COALESCE(
                                (v_json ->> 'application_date')::DATE,
                                current_date
                                  );

                        insert into application (
                            lot_id,
                            medicine_batch_id,
                            user_id,
                            application_date,
                            application_time,
                            dose,
                            unit,
                            route,
                            head_count_applied,
                            meat_withdrawal_end,
                            applied_limb,
                            applied_site,
                            goal,
                            notes,
                            active,
                            source_id,
                            synced_at
                        )
                        select
                            v_pai,
                            v_aux,
                            COALESCE(
                                    fn_de_para(
                                            'app_user',
                                            v_json ->> 'source_user_id'
                                    ),
                                    v_rpa
                            ),
                            v_data,
                            (v_json ->> 'application_time')::TIME,
                            (v_json ->> 'dose')::NUMERIC,
                            v_texto,
                            fn_de_para_enum(
                                    v_json ->> 'route',
                                    ARRAY[
                                        'INTRAMUSCULAR',
                                        'SUBCUTANEA',
                                        'ORAL',
                                        'TOPICA',
                                        'INTRAVENOSA'
                                        ],
                                    'INTRAMUSCULAR'
                            ),
                            COALESCE(
                                    (v_json ->> 'head_count_applied')::INTEGER,
                                    1
                            ),
                            fn_data_fim_carencia(
                                    mb.medicine_id,
                                    v_data
                            ),
                            left(
                                    v_json ->> 'applied_limb',
                                    50
                            ),
                            left(
                                    v_json ->> 'applied_site',
                                    100
                            ),
                            left(
                                    v_json ->> 'goal',
                                    120
                            ),
                            v_json ->> 'notes',
                            COALESCE(
                                    (v_json ->> 'active')::BOOLEAN,
                                    true
                            ),
                            v_source::UUID,
                            current_timestamp
                        from medicine_batch mb
                        where mb.id = v_aux
                        returning id into v_target;

                        v_novo := true;

                    else

                        update application
                        set goal =
                                left(
                                        v_json ->> 'goal',
                                        120
                                ),
                            notes =
                                v_json ->> 'notes',
                            active =
                                COALESCE(
                                        (v_json ->> 'active')::BOOLEAN,
                                        true
                                ),
                            synced_at = current_timestamp
                        where id = v_target;

                    end if;



                    -- caso a tabela nn for mapeada


                else
                    raise EXCEPTION
                        'Tabela de origem nao mapeada: %',
                        v_tabela;
                end if;



                -- contadores


                if v_novo then
                    v_inseridos := v_inseridos + 1;
                else
                    v_atualizado := v_atualizado + 1;
                end if;



                -- mapeamento

                insert into load_mapping (
                    table_name,
                    source_id,
                    target_id,
                    natural_key
                )
                values (
                           v_tabela,
                           v_source,
                           v_target,
                           COALESCE(v_chave, v_source)
                       )
                on CONFLICT (
                    table_name,
                    source_id
                    )
                    do NOTHING;



                -- processamento de staging


                update load_staging
                set processed = true
                where id = r.id;


            EXCEPTION
                when OTHERS then

                    v_rejeitados := v_rejeitados + 1;

                    insert into load_error (
                        load_run_id,
                        source_table,
                        natural_key,
                        original_content,
                        error_message
                    )
                    values (
                               p_load_run_id,
                               COALESCE(
                                       v_tabela,
                                       'DESCONHECIDA'
                               ),
                               COALESCE(
                                       v_chave,
                                       v_source,
                                       r.row_hash
                               ),
                               COALESCE(
                                       v_json,
                                       jsonb_build_object(
                                               'raw',
                                               r.source_columns
                                       )
                               ),
                               SQLERRM
                           );

            end;

        end loop;



    update lot l
    set head_count = (
        select COUNT(*)::INTEGER
        from animal_exception ae
        where ae.lot_id = l.id
          and ae.status = 'ATIVO'
    )
    where l.is_exception_lot = true;

    update lot
    set active = true,
        close_date = null
    where is_exception_lot = true
      and head_count > 0;

    update lot l
    set open_date = LEAST(l.open_date, h.primeira_entrada)
    from (
             select lot_id, MIN(entry_date) as primeira_entrada
             from animal_lot_history
             group by lot_id
         ) h
    where l.id = h.lot_id
      and l.is_exception_lot = true;

    update lot l
    set active = false,
        close_date = COALESCE(
                (
                    select MAX(h.exit_date)
                    from animal_lot_history h
                    where h.lot_id = l.id
                ),
                l.close_date,
                current_date
                     )
    where l.is_exception_lot = true
      and l.head_count = 0;



    -- recalculo de carencia de lote


    update lot l
    set slaughter_release_date = c.maior,
        fit_for_slaughter =
            (
                c.maior is null
                    or c.maior <= current_date
                )
    from (
             select
                 ap.lot_id,
                 MAX(ap.meat_withdrawal_end) as maior
             from application ap
             group by ap.lot_id
         ) c
    where c.lot_id = l.id;


   --atrualizações

    update load_run
    set end_time = current_timestamp,
        rows_read = v_lidos,
        rows_inserted = v_inseridos,
        rows_updated = v_atualizado,
        rows_rejected = v_rejeitados,
        loaded_until = current_timestamp,
        status =
            case
                when v_rejeitados = 0
                    then 'SUCESSO'
                when v_rejeitados = v_lidos
                    then 'ERRO'
                else 'PARCIAL'
                end
    where id = p_load_run_id;


    perform set_config(
            'app.carga',
            'false',
            true
            );

    commit;

end;
$$ language plpgsql;

