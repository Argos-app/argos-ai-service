-- tabelas de identidade e acesso


create table permission
(
    id          UUID primary key     default gen_random_uuid(),
    code        VARCHAR(30) not null unique,
    name        VARCHAR(40) not null,
    description TEXT,
    active      BOOLEAN     not null default true,
    constraint ck_permission_code check (code = UPPER(code))
);

create table app_user
(
    id            UUID primary key      default gen_random_uuid(),
    permission_id UUID         not null references permission (id) on delete restrict,
    name          VARCHAR(120) not null,
    cpf           CHAR(11)     not null unique,
    email         VARCHAR(120) not null unique,
    firebase_uid  VARCHAR(128),

    photo_url     TEXT,

    active        BOOLEAN      not null default true,
    first_login   BOOLEAN      not null default true,
    blocked_until TIMESTAMP,
    source_id     UUID,
    created_at    TIMESTAMP    not null default current_timestamp,
    updated_at    TIMESTAMP    not null default current_timestamp,

    constraint ck_app_user_cpf check (cpf ~ '^[0-9]{11}$'),
    constraint ck_app_user_email check (email like '%_@_%._%')
);

create table property
(
    id            UUID primary key      default gen_random_uuid(),
    user_id       UUID         not null references app_user (id) on delete restrict,
    name          VARCHAR(120) not null,
    cnpj          CHAR(14),
    street        VARCHAR(150),
    number        VARCHAR(10),
    complement    VARCHAR(100),
    zip_code      CHAR(8),
    address_full  TEXT generated always as (COALESCE(street, '') || ', ' || COALESCE(number, 'S/N') ||
                                            case when complement is null then '' else ' - ' || complement end) stored,
    city          VARCHAR(100),
    state         CHAR(2),
    area_hectares NUMERIC(10, 2),
    phone         VARCHAR(20),
    source_id     UUID,
    active        BOOLEAN      not null default true,
    created_at    TIMESTAMP    not null default current_timestamp,
    updated_at    TIMESTAMP    not null default current_timestamp,
    constraint ck_property_cnpj check (cnpj is null or cnpj ~ '^[0-9]{14}$'),
    constraint ck_property_cep check (zip_code is null or zip_code ~ '^[0-9]{8}$'),
    constraint ck_property_area check (area_hectares is null or area_hectares > 0),
    constraint ck_property_uf check (state is null or state in
                                     ('AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG',
                                      'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE',
                                      'TO'))
);


create table user_property
(
    user_id     UUID    not null references app_user (id) on delete cascade,
    property_id UUID    not null references property (id) on delete cascade,
    link_date   DATE    not null default current_date,
    unlink_date DATE,
    active      BOOLEAN not null default true,
    primary key (user_id, property_id),
    constraint ck_user_property_datas check (unlink_date is null or unlink_date >= link_date)
);

create table device
(
    id         UUID primary key      default gen_random_uuid(),
    user_id    UUID         not null references app_user (id) on delete cascade,
    push_token VARCHAR(255) not null unique,
    platform   VARCHAR(10)  not null,
    model      VARCHAR(60),
    last_sync  TIMESTAMP,
    active     BOOLEAN      not null default true,
    created_at TIMESTAMP    not null default current_timestamp,
    constraint ck_device_platform check (platform in ('ANDROID', 'IOS'))
);


-- tabelas legadas do 1o ano (rpa)


create table herd
(
    id          UUID primary key     default gen_random_uuid(),
    property_id UUID        not null references property (id) on delete restrict,
    herd_name   VARCHAR(60) not null,
    breed       VARCHAR(40) not null,
    head_count  INTEGER     not null default 0,
    source_id   UUID,
    active      BOOLEAN     not null default true,
    created_at  TIMESTAMP   not null default current_timestamp,
    updated_at  TIMESTAMP   not null default current_timestamp,
    constraint uq_herd_nome unique (property_id, herd_name),
    constraint ck_herd_count check (head_count >= 0)
);



create table lot
(
    id                     UUID primary key     default gen_random_uuid(),
    herd_id                UUID        not null references herd (id) on delete restrict,
    parent_lot_id          UUID        references lot (id) on delete set null,
    lot_code               VARCHAR(20) not null,
    category               VARCHAR(30) not null,
    is_exception_lot       BOOLEAN     not null default false,
    exception_reason       VARCHAR(255),
    head_count             INTEGER     not null default 0,
    slaughter_release_date DATE,
    fit_for_slaughter      BOOLEAN     not null default true,
    open_date              DATE        not null default current_date,
    close_date             DATE,
    source_id              UUID,
    active                 BOOLEAN     not null default true,
    created_at             TIMESTAMP   not null default current_timestamp,
    updated_at             TIMESTAMP   not null default current_timestamp,
    constraint uq_lot_code unique (herd_id, lot_code),
    constraint ck_lot_datas check (close_date is null or close_date >= open_date),
    constraint ck_lot_count check (head_count >= 0),
    constraint ck_lot_nao_ciclo check (parent_lot_id is null or parent_lot_id <> id),
    constraint ck_lot_excecao check (is_exception_lot = false or parent_lot_id is not null),
    constraint ck_lot_excecao_motivo check (is_exception_lot = false or exception_reason is not null),
    constraint ck_lot_category check (category in ('ENGORDA', 'CRIA', 'RECRIA'))
);



create table animal_exception
(
    id                     UUID primary key      default gen_random_uuid(),
    lot_id                 UUID         not null references lot (id) on delete restrict,
    origin_lot_id          UUID         not null references lot (id) on delete restrict,
    ear_tag                VARCHAR(20)  not null,
    category               VARCHAR(20)  not null default 'BOI',
    birth_date             DATE,
    current_weight         NUMERIC(7, 2),
    exception_date         DATE         not null default current_date,
    exception_end_date     DATE,
    exception_reason       VARCHAR(255) not null,
    slaughter_release_date DATE,
    fit_for_slaughter      BOOLEAN      not null default true,
    status                 VARCHAR(15)  not null default 'ATIVO',
    notes                  TEXT,
    source_id              UUID,
    created_at             TIMESTAMP    not null default current_timestamp,
    updated_at             TIMESTAMP    not null default current_timestamp,
    constraint ck_animal_exception_status check (status in ('ATIVO', 'VENDIDO', 'ABATIDO', 'MORTO', 'RECUPERADO')),
    constraint ck_animal_exception_peso check (current_weight is null or current_weight > 0),
    constraint ck_animal_exception_nascto check (birth_date is null or birth_date <= current_date),
    constraint ck_animal_exception_category check (category in ('BEZERRO', 'GARROTE', 'BOI', 'TOURO')),
    constraint ck_animal_exception_fim check (exception_end_date is null or exception_end_date >= exception_date)
);

create table supplier
(
    id           UUID primary key      default gen_random_uuid(),
    company_name VARCHAR(120) not null,
    cnpj         CHAR(14)     not null unique,
    email        VARCHAR(120),
    phone        VARCHAR(20),
    address      VARCHAR(255),
    source_id    UUID,
    active       BOOLEAN      not null default true,
    created_at   TIMESTAMP    not null default current_timestamp,
    updated_at   TIMESTAMP    not null default current_timestamp,
    constraint ck_supplier_cnpj check (cnpj ~ '^[0-9]{14}$')
);

create table therapeutic_category
(
    id          UUID primary key default gen_random_uuid(),
    name        VARCHAR(40) not null unique,
    name_norm   TEXT generated always as (fn_norm_texto(name)) stored,
    description TEXT,
    constraint uq_therapeutic_category_norm unique (name_norm)
);

create table medicine
(
    id                      UUID primary key        default gen_random_uuid(),
    supplier_id             UUID           not null references supplier (id) on delete restrict,
    therapeutic_category_id UUID           not null references therapeutic_category (id) on delete restrict,
    brand_name              VARCHAR(80)    not null,
    active_ingredient       VARCHAR(80)    not null,
    mapa_registration       VARCHAR(20),
    indication              TEXT           not null,
    description             TEXT,
    needs_prescription      BOOLEAN        not null default false,
    meat_withdrawal_days    INTEGER        not null default 0,
    dose                    NUMERIC(10, 3) not null,
    unit                    VARCHAR(10)    not null,
    route                   VARCHAR(20),
    source_id               UUID,
    active                  BOOLEAN        not null default true,
    created_at              TIMESTAMP      not null default current_timestamp,
    updated_at              TIMESTAMP      not null default current_timestamp,
    constraint ck_medicine_carencia check (meat_withdrawal_days >= 0),
    constraint ck_medicine_dose check (dose > 0),
    constraint ck_medicine_unit check (unit in ('ml', 'mg', 'g', 'UI', 'dose')),
    constraint ck_medicine_route check (route is null or
                                        route in ('INTRAMUSCULAR', 'SUBCUTANEA', 'ORAL', 'TOPICA', 'INTRAVENOSA'))
);

create table medicine_batch
(
    id               UUID primary key        default gen_random_uuid(),
    medicine_id      UUID           not null references medicine (id) on delete restrict,
    batch_number     VARCHAR(30)    not null,
    manufacture_date DATE           not null,
    expiry_date      DATE           not null,
    delivery_date    DATE           not null,
    initial_quantity NUMERIC(10, 3) not null,
    min_stock        NUMERIC(10, 3),
    max_stock        NUMERIC(10, 3),
    storage_place    VARCHAR(60),
    status           VARCHAR(15)    not null default 'DISPONIVEL',
    source_id        UUID,
    created_at       TIMESTAMP      not null default current_timestamp,
    updated_at       TIMESTAMP      not null default current_timestamp,
    constraint uq_medicine_batch unique (medicine_id, batch_number),
    constraint ck_medicine_batch_qt check (initial_quantity > 0),
    constraint ck_medicine_batch_dt check (expiry_date > manufacture_date),
    constraint ck_medicine_batch_st check (status in ('DISPONIVEL', 'VENCIDO', 'ESGOTADO', 'RECOLHIDO')),
    constraint ck_medicine_batch_minmax check (min_stock is null or max_stock is null or max_stock >= min_stock)
);

-- tabels operacionais
create table application
(
    id                  UUID primary key        default gen_random_uuid(),
    lot_id              UUID           not null references lot (id) on delete restrict,
    medicine_batch_id   UUID           not null references medicine_batch (id) on delete restrict,
    user_id             UUID           not null references app_user (id) on delete restrict,
    application_date    DATE           not null default current_date,
    application_time    TIME,
    dose                NUMERIC(10, 3) not null,
    unit                VARCHAR(10)    not null,
    route               VARCHAR(20)    not null,
    head_count_applied  INTEGER        not null,
    meat_withdrawal_end DATE,
    applied_limb        VARCHAR(50),
    applied_site        VARCHAR(100),
    goal                VARCHAR(120),
    notes               TEXT,
    active              BOOLEAN        not null default true,
    source_id           UUID,
    synced_at           TIMESTAMP,
    created_at          TIMESTAMP      not null default current_timestamp,
    updated_at          TIMESTAMP      not null default current_timestamp,
    constraint ck_application_dose check (dose > 0),
    constraint ck_application_count check (head_count_applied > 0),
    constraint ck_application_data check (application_date <= current_date),
    constraint ck_application_unit check (unit in ('ml', 'mg', 'g', 'UI', 'dose')),
    constraint ck_application_route check (route in ('INTRAMUSCULAR', 'SUBCUTANEA', 'ORAL', 'TOPICA', 'INTRAVENOSA'))
);


create table application_animal
(
    id                     UUID primary key        default gen_random_uuid(),
    application_id         UUID           not null references application (id) on delete cascade,
    animal_exception_id    UUID           not null references animal_exception (id) on delete restrict,
    dose_given             NUMERIC(10, 3) not null,
    weight_at_time         NUMERIC(7, 2),
    meat_withdrawal_end    DATE,
    released_for_slaughter BOOLEAN        not null default false,
    created_at             TIMESTAMP      not null default current_timestamp,
    constraint uq_application_animal unique (application_id, animal_exception_id),
    constraint ck_application_animal_dose check (dose_given > 0)
);

create table stock_movement
(
    id                UUID primary key        default gen_random_uuid(),
    medicine_batch_id UUID           not null references medicine_batch (id) on delete restrict,
    application_id    UUID           references application (id) on delete set null,
    user_id           UUID           not null references app_user (id) on delete restrict,
    type              CHAR(1)        not null,
    quantity          NUMERIC(10, 3) not null,
    movement_date     TIMESTAMP      not null default current_timestamp,
    reason            VARCHAR(60)    not null,
    source_id         UUID,
    created_at        TIMESTAMP      not null default current_timestamp,
    constraint ck_stock_movement_type check (type in ('E', 'S')),
    constraint ck_stock_movement_qt check (quantity > 0),
    constraint ck_stock_movement_reason check (reason in
                                               ('APLICACAO', 'CARGA_INICIAL', 'COMPRA', 'PERDA', 'VENCIMENTO',
                                                'AJUSTE'))
);

create table occurrence
(
    id                  UUID primary key     default gen_random_uuid(),
    animal_exception_id UUID        not null references animal_exception (id) on delete restrict,
    user_id             UUID        not null references app_user (id) on delete restrict,
    type                VARCHAR(20) not null,
    occurrence_date     DATE        not null default current_date,
    description         TEXT        not null,
    diagnosis           TEXT,
    resolved            BOOLEAN     not null default false,
    resolved_at         DATE,
    created_at          TIMESTAMP   not null default current_timestamp,
    constraint ck_occurrence_type check (type in ('DOENCA', 'MORTE', 'REACAO_ADVERSA', 'FERIMENTO')),
    constraint ck_occurrence_resolved check (resolved = false or resolved_at is not null)
);


create table animal_lot_history
(
    id                  UUID primary key     default gen_random_uuid(),
    animal_exception_id UUID        not null references animal_exception (id) on delete cascade,
    lot_id              UUID        not null references lot (id) on delete restrict,
    entry_date          DATE        not null default current_date,
    exit_date           DATE,
    reason              VARCHAR(60) not null,
    created_at          TIMESTAMP   not null default current_timestamp,
    constraint ck_animal_lot_history_datas check (exit_date is null or exit_date >= entry_date)
);

create table alert
(
    id                  UUID primary key      default gen_random_uuid(),
    user_id             UUID         not null references app_user (id) on delete cascade,
    lot_id              UUID references lot (id) on delete cascade,
    animal_exception_id UUID references animal_exception (id) on delete cascade,
    type                VARCHAR(30)  not null,
    severity            VARCHAR(10)  not null default 'MEDIA',
    title               VARCHAR(120) not null,
    message             TEXT         not null,
    created_at          TIMESTAMP    not null default current_timestamp,
    read_at             TIMESTAMP,
    status              VARCHAR(15)  not null default 'NOVO',
    constraint ck_alert_type check (type in ('CARENCIA', 'VALIDADE', 'ESTOQUE_BAIXO', 'OCORRENCIA')),
    constraint ck_alert_severity check (severity in ('BAIXA', 'MEDIA', 'ALTA')),
    constraint ck_alert_status check (status in ('NOVO', 'LIDO', 'RESOLVIDO'))
);

create table notification_delivery
(
    id        UUID primary key     default gen_random_uuid(),
    alert_id  UUID        not null references alert (id) on delete cascade,
    device_id UUID        not null references device (id) on delete cascade,
    channel   VARCHAR(15) not null,
    status    VARCHAR(15) not null default 'PENDENTE',
    attempts  INTEGER     not null default 0,
    sent_at   TIMESTAMP,
    error     TEXT,
    constraint ck_notification_channel check (channel in ('PUSH', 'SMS', 'EMAIL')),
    constraint ck_notification_status check (status in ('PENDENTE', 'ENVIADO', 'FALHOU'))
);


create table attachment
(
    id         UUID primary key      default gen_random_uuid(),
    user_id    UUID         not null references app_user (id) on delete restrict,
    entity     VARCHAR(30)  not null,
    entity_id  UUID         not null,
    type       VARCHAR(20)  not null,
    file_path  VARCHAR(255) not null,
    file_hash  CHAR(64)     not null,
    size_bytes INTEGER      not null,
    created_at TIMESTAMP    not null default current_timestamp,
    constraint ck_attachment_type check (type in ('FOTO_BRINCO', 'BULA', 'NOTA_FISCAL', 'LAUDO', 'RECEITA')),
    constraint ck_attachment_size check (size_bytes > 0)
);

-- auditoria e monitoramento de carga

create table change_audit
(
    id          UUID primary key     default gen_random_uuid(),
    table_name  VARCHAR(60) not null,
    operation   CHAR(6)     not null,
    record_id   VARCHAR(40) not null,
    old_data    JSONB,
    new_data    JSONB,
    db_user     VARCHAR(60) not null,
    app_user_id UUID,
    ip_address  INET,
    happened_at TIMESTAMP   not null default current_timestamp,
    constraint ck_change_audit_op check (operation in ('INSERT', 'UPDATE', 'DELETE'))
);

create table access_audit
(
    id             UUID primary key      default gen_random_uuid(),
    user_id        UUID         references app_user (id) on delete set null,
    login_tried    VARCHAR(120) not null,
    success        BOOLEAN      not null,
    failure_reason VARCHAR(60),
    source         VARCHAR(10)  not null default 'MOBILE',
    ip_address     INET,
    user_agent     VARCHAR(255),
    happened_at    TIMESTAMP    not null default current_timestamp,
    constraint ck_access_audit_motivo check (
        failure_reason is null or failure_reason in
                                  ('SENHA_ERRADA', 'CONTA_BLOQUEADA', 'USUARIO_INEXISTENTE', 'TOKEN_INVALIDO')
        ),
    constraint ck_access_audit_source check (source in ('WEB', 'MOBILE', 'API'))
);


create table interaction_audit
(
    id                  UUID primary key     default gen_random_uuid(),
    user_id             UUID        not null references app_user (id) on delete cascade,
    device_id           UUID        references device (id) on delete set null,
    action_type         VARCHAR(20) not null,
    element_type        VARCHAR(20) not null,
    element_id          VARCHAR(60) not null,
    element_name        VARCHAR(60) not null,
    element_description VARCHAR(120),
    created_at          TIMESTAMP   not null default current_timestamp,
    constraint ck_interaction_action check (action_type in ('CLIQUE', 'ENTRAR_TELA', 'SAIR_TELA', 'OCULTAR', 'SCROLL')),
    constraint ck_interaction_element check (element_type in ('BOTAO', 'ACTIVITY', 'FRAGMENT', 'CAMPO', 'LISTA'))
);

create table daily_access
(
    id               UUID primary key     default gen_random_uuid(),
    user_id          UUID        not null references app_user (id) on delete cascade,
    date             DATE        not null default current_date,
    source           VARCHAR(10) not null,
    session_count    INTEGER     not null default 1,
    first_login_time TIMESTAMP   not null default current_timestamp,
    constraint uq_daily_access unique (user_id, date, source),
    constraint ck_daily_access_src check (source in ('WEB', 'MOBILE', 'API'))
);

create table data_catalog
(
    id               UUID primary key     default gen_random_uuid(),
    table_name       VARCHAR(60) not null,
    column_name      VARCHAR(60) not null,
    data_type        VARCHAR(30) not null,
    description      TEXT        not null,
    business_rule    TEXT,
    access_level     VARCHAR(15) not null default 'INTERNO',
    personal_data    BOOLEAN     not null default false,
    lgpd_legal_basis VARCHAR(60),
    updated_at       TIMESTAMP   not null default current_timestamp,
    constraint uq_data_catalog unique (table_name, column_name),
    constraint ck_data_catalog_nivel check (access_level in ('PUBLICO', 'INTERNO', 'RESTRITO', 'SENSIVEL')),
    constraint ck_data_catalog_lgpd check (personal_data = false or lgpd_legal_basis is not null)
);


create table load_run
(
    id            UUID primary key     default gen_random_uuid(),
    routine_name  VARCHAR(60) not null,
    source        VARCHAR(40) not null,
    start_time    TIMESTAMP   not null default current_timestamp,
    end_time      TIMESTAMP,
    rows_read     INTEGER     not null default 0,
    rows_inserted INTEGER     not null default 0,
    rows_updated  INTEGER     not null default 0,
    rows_rejected INTEGER     not null default 0,
    status        VARCHAR(15) not null default 'EXECUTANDO',
    loaded_until  TIMESTAMP,
    message       TEXT,
    constraint ck_load_run_status check (status in ('EXECUTANDO', 'SUCESSO', 'ERRO', 'PARCIAL'))
);


create table load_error
(
    id               UUID primary key      default gen_random_uuid(),
    load_run_id      UUID         not null references load_run (id) on delete cascade,
    source_table     VARCHAR(60)  not null,
    natural_key      VARCHAR(120) not null,
    original_content JSONB        not null,
    error_message    TEXT         not null,
    attempts         INTEGER      not null default 1,
    reprocessed      BOOLEAN      not null default false
);


create table load_mapping
(
    id          UUID primary key      default gen_random_uuid(),
    table_name  VARCHAR(60)  not null,
    source_id   VARCHAR(40)  not null,
    target_id   UUID         not null,
    natural_key VARCHAR(120) not null,
    load_date   TIMESTAMP    not null default current_timestamp,
    constraint uq_load_mapping unique (table_name, source_id)
);


create table load_staging
(
    id             UUID primary key   default gen_random_uuid(),
    load_run_id    UUID      not null references load_run (id) on delete cascade,
    source_columns TEXT      not null,
    row_hash       CHAR(64)  not null,
    processed      BOOLEAN   not null default false,
    extract_date   TIMESTAMP not null default current_timestamp,
    constraint uq_load_staging unique (load_run_id, row_hash)
);
