-- Massa fictícia de homologação para TODAS as 30 tabelas do esquema atual.
-- Executar os módulos 01 a 09 antes deste arquivo e executar este arquivo inteiro.
-- Os triggers permanecem ativos, inclusive suas regras de negócio: app.carga=false.
-- Não chama sp_carga_rpa, pois essa procedure ativa app.carga=true internamente.
-- Uma transação: qualquer falha inesperada desfaz a carga inteira.
-- ATENÇÃO: cada execução REMOVE TODOS os registros das 30 tabelas e gera a massa novamente.
-- TRUNCATE não dispara triggers de DELETE; todos continuam habilitados na inserção.
-- A massa tem duas séries independentes; papéis, categorias e catálogo não são duplicados.

BEGIN;
SET LOCAL app.carga = 'false';

DO $pre_condicoes$
DECLARE
    r RECORD;
BEGIN
    IF current_setting('session_replication_role') <> 'origin' THEN
        RAISE EXCEPTION 'Execute com session_replication_role=origin para preservar os triggers.';
    END IF;

    FOR r IN SELECT unnest(ARRAY[
        'permission', 'app_user', 'user_property', 'device', 'property', 'herd',
        'lot', 'animal_exception', 'supplier', 'therapeutic_category', 'medicine',
        'medicine_batch', 'application', 'application_animal', 'stock_movement',
        'occurrence', 'animal_lot_history', 'alert', 'notification_delivery',
        'attachment', 'change_audit', 'access_audit', 'interaction_audit',
        'daily_access', 'data_catalog', 'load_run', 'load_error', 'load_mapping',
        'load_staging', 'dim_date'
    ]) AS tabela LOOP
        IF to_regclass(r.tabela) IS NULL THEN
            RAISE EXCEPTION 'Tabela % ausente. Execute os módulos 01 a 09 primeiro.', r.tabela;
        END IF;
        IF NOT has_table_privilege(to_regclass(r.tabela), 'TRUNCATE') THEN
            RAISE EXCEPTION 'O usuário precisa de permissão TRUNCATE na tabela %.', r.tabela;
        END IF;
    END LOOP;

    FOR r IN SELECT * FROM (VALUES
        ('app_user', 'trg_atualiza_data_app_user'),
        ('property', 'trg_atualiza_data_property'),
        ('herd', 'trg_atualiza_data_herd'),
        ('lot', 'trg_atualiza_data_lot'),
        ('animal_exception', 'trg_atualiza_data_animal_exception'),
        ('supplier', 'trg_atualiza_data_supplier'),
        ('medicine', 'trg_atualiza_data_medicine'),
        ('medicine_batch', 'trg_atualiza_data_medicine_batch'),
        ('application', 'trg_atualiza_data_application'),
        ('data_catalog', 'trg_atualiza_data_data_catalog'),
        ('lot', 'trg_valida_hierarquia_lote'),
        ('lot', 'trg_atualiza_contagem_rebanho'),
        ('animal_exception', 'trg_valida_animal_exception'),
        ('application', 'trg_auditoria_application'),
        ('stock_movement', 'trg_auditoria_stock_movement'),
        ('application', 'trg_calcula_carencia_lote'),
        ('application', 'trg_protege_aplicacao'),
        ('application', 'trg_atualiza_apto_abate_lote'),
        ('application_animal', 'trg_calcula_carencia_excecao'),
        ('application_animal', 'trg_protege_application_animal'),
        ('application', 'trg_baixa_estoque'),
        ('stock_movement', 'trg_protege_movimento_estoque'),
        ('stock_movement', 'trg_valida_saldo_movimento'),
        ('application', 'trg_gera_alerta'),
        ('animal_exception', 'trg_atualiza_contagem'),
        ('animal_exception', 'trg_protege_exclusao_animal_exception'),
        ('access_audit', 'trg_registra_acesso_diario')
    ) AS esperados(tabela, nome) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgrelid = to_regclass(r.tabela)
              AND tgname = r.nome AND tgenabled IN ('O', 'A')
        ) THEN
            RAISE EXCEPTION 'Trigger % de % ausente ou inativo.', r.nome, r.tabela;
        END IF;
    END LOOP;

END;
$pre_condicoes$;

-- Objetos temporários auxiliam a carga e não acrescentam tabelas ao esquema.
CREATE TEMP TABLE _argos_resumo (
    tabela TEXT PRIMARY KEY, descricao TEXT NOT NULL,
    antes BIGINT DEFAULT 0, depois BIGINT DEFAULT 0
) ON COMMIT DROP;
INSERT INTO _argos_resumo (tabela, descricao) VALUES
('permission', 'Papel de acesso do usuário'),
('app_user', 'Cadastro e identidade do usuário'),
('user_property', 'Vínculo de acesso do usuário à propriedade'),
('device', 'Dispositivo móvel e token de notificações'),
('property', 'Propriedade rural'),
('herd', 'Rebanho agrupado por raça'),
('lot', 'Lote de manejo ou sublote de exceção'),
('animal_exception', 'Animal acompanhado individualmente por exceção'),
('supplier', 'Fornecedor de medicamentos'),
('therapeutic_category', 'Classificação terapêutica do medicamento'),
('medicine', 'Cadastro do medicamento e informações de bula'),
('medicine_batch', 'Lote de fabricação e armazenamento do medicamento'),
('application', 'Aplicação de medicamento em lote'),
('application_animal', 'Dose individual e peso do animal na aplicação'),
('stock_movement', 'Entrada ou saída de medicamento'),
('occurrence', 'Ocorrência veterinária do animal de exceção'),
('animal_lot_history', 'Permanência do animal em um lote'),
('alert', 'Aviso disponível no aplicativo'),
('notification_delivery', 'Tentativa de entrega externa de um alerta'),
('attachment', 'Metadados de anexo de uma entidade'),
('change_audit', 'Auditoria automática de alterações'),
('access_audit', 'Tentativa de autenticação'),
('interaction_audit', 'Interação do usuário com a interface'),
('daily_access', 'Agregado diário de sessões bem-sucedidas'),
('data_catalog', 'Dicionário das colunas do esquema atual'),
('load_run', 'Execução de importação e seus totais'),
('load_error', 'Linha rejeitada e motivo para reprocessamento'),
('load_mapping', 'Correspondência entre ID de origem e ID de destino'),
('load_staging', 'Conteúdo bruto recebido para importação'),
('dim_date', 'Calendário da camada de BI');

DO $contagem_inicial$
DECLARE r RECORD; v_total BIGINT;
BEGIN
    FOR r IN SELECT tabela FROM _argos_resumo LOOP
        EXECUTE format('SELECT count(*) FROM %s', to_regclass(r.tabela)) INTO v_total;
        UPDATE _argos_resumo SET antes = v_total WHERE tabela = r.tabela;
    END LOOP;
END;
$contagem_inicial$;

-- Uma única limpeza, sem CASCADE: não atinge outras tabelas fora desta lista.
-- TRUNCATE é transacional: uma falha posterior restaura inclusive os dados antigos.
DO $limpeza$
DECLARE v_tabelas TEXT;
BEGIN
    SELECT string_agg(to_regclass(tabela)::TEXT, ', ' ORDER BY tabela)
    INTO v_tabelas FROM _argos_resumo;
    EXECUTE 'TRUNCATE TABLE ' || v_tabelas || ' RESTRICT';
END;
$limpeza$;

-- Uma execução nova gera IDs novos; referências da mesma série usam o mesmo ID.
SELECT set_config('app.massa_execucao', gen_random_uuid()::TEXT, true);
CREATE OR REPLACE FUNCTION pg_temp.argos_id(p_entidade TEXT, p_numero INTEGER)
RETURNS UUID LANGUAGE SQL STABLE AS $id$
    SELECT CASE
        WHEN current_setting('app.massa_grupo') = '1'
             AND p_entidade = 'usuario' AND p_numero = 1
            THEN '00000000-0000-4000-8000-000000000001'::UUID
        WHEN current_setting('app.massa_grupo') = '1'
             AND p_entidade = 'propriedade' AND p_numero = 1
            THEN '00000000-0000-4000-8000-000000000101'::UUID
        WHEN current_setting('app.massa_grupo') = '1'
             AND p_entidade = 'propriedade' AND p_numero = 2
            THEN '00000000-0000-4000-8000-000000000102'::UUID
        ELSE md5(current_setting('app.massa_execucao') || ':' || current_setting('app.massa_grupo')
                 || ':' || p_entidade || ':' || p_numero::TEXT)::UUID
    END;
$id$;

-- Duas séries com IDs, documentos e tokens independentes.
DO $massa_em_dobro$
DECLARE v_grupo INTEGER;
BEGIN
    FOR v_grupo IN 1..2 LOOP
        PERFORM set_config('app.massa_grupo', v_grupo::TEXT, true);
        EXECUTE $inserts_da_serie$
-- 1. Papéis e categorias: recria os códigos válidos do módulo 03 após a limpeza.
INSERT INTO permission (code, name, description) VALUES
('ADMIN', 'Administrador', 'Acesso administrativo'),
('GERENTE', 'Gerente', 'Gestão das propriedades vinculadas'),
('PECUARISTA', 'Pecuarista', 'Responsável pela propriedade'),
('APLICADOR', 'Aplicador', 'Registro de tratamentos no campo'),
('SISTEMA', 'Sistema', 'Identidade técnica de integração')
ON CONFLICT (code) DO NOTHING;

INSERT INTO therapeutic_category (name, description) VALUES
('Antibiótico', 'Tratamento de infecções bacterianas'),
('Anti-inflamatório', 'Controle de inflamação'),
('Antiparasitário', 'Controle de parasitas'),
('Vacina', 'Imunização'),
('Vitamínico', 'Suplementação vitamínica'),
('Hormonal', 'Protocolos hormonais'),
('Anestésico', 'Anestesia e sedação'),
('Cicatrizante', 'Cicatrização'),
('Ectoparasiticida', 'Controle de parasitas externos'),
('Suplemento Mineral', 'Suplementação mineral'),
('Não informado', 'Categoria não reconhecida na importação')
ON CONFLICT DO NOTHING;

INSERT INTO app_user (permission_id, name, cpf, email, firebase_uid, first_login)
SELECT id, 'RPA Argos série ' || current_setting('app.massa_grupo'),
       '0000000000' || (current_setting('app.massa_grupo')::INTEGER - 1)::TEXT,
       'rpa' || current_setting('app.massa_grupo') || '@argos.test',
       'rpa-sistema-' || current_setting('app.massa_grupo'), false
FROM permission WHERE code = 'SISTEMA'
ON CONFLICT (cpf) DO NOTHING;

-- 15 pecuaristas, 15 aplicadores, um administrador e um gerente.
INSERT INTO app_user (id, permission_id, name, cpf, email, firebase_uid,
                      first_login, active, source_id)
SELECT pg_temp.argos_id('usuario', n), p.id,
       CASE WHEN n <= 15 THEN (ARRAY[
           'Ana Silva', 'Bruno Costa', 'Carlos Eduardo', 'Daniela Rocha', 'Eduardo Lima',
           'Fernanda Souza', 'Gabriel Alves', 'Helena Martins', 'Igor Ferreira',
           'Juliana Paes', 'Lucas Mendes', 'Mariana Oliveira', 'Nicolas Santos',
           'Patricia Ribeiro', 'Rafael Barbosa'])[n]
            WHEN n <= 30 THEN 'Aplicador de homologação ' || (n - 15)::TEXT
            WHEN n = 31 THEN 'Administrador de homologação'
            ELSE 'Gerente de homologação' END,
       '99' || current_setting('app.massa_grupo') || '000000' || lpad(n::TEXT, 2, '0'),
       'carga.completa.g' || current_setting('app.massa_grupo') || '.' || lpad(n::TEXT, 2, '0') || '@argos.test',
       'homologacao-argos-completa-g' || current_setting('app.massa_grupo') || '-' || n::TEXT, false, true,
       CASE WHEN n <= 30 THEN pg_temp.argos_id('origem_usuario', n) END
FROM generate_series(1, 32) n
JOIN permission p ON p.code = CASE WHEN n <= 15 THEN 'PECUARISTA'
    WHEN n <= 30 THEN 'APLICADOR' WHEN n = 31 THEN 'ADMIN' ELSE 'GERENTE' END;

SELECT set_config('app.usuario', pg_temp.argos_id('usuario', 31)::TEXT, true);
SET LOCAL app.ip = '127.0.0.1';

INSERT INTO property (id, user_id, name, cnpj, street, number, complement,
                      zip_code, city, state, area_hectares, phone, source_id)
SELECT pg_temp.argos_id('propriedade', n), pg_temp.argos_id('usuario', n),
       (ARRAY['Fazenda Sol Nascente', 'Sítio das Águas', 'Fazenda Santa Maria',
              'Estância Boi Gordo', 'Fazenda Monte Verde', 'Sítio Recanto Feliz',
              'Fazenda Palmeiras', 'Fazenda Planalto', 'Sítio Boa Vista',
              'Fazenda Vale Verde', 'Estância Querência', 'Fazenda São João',
              'Sítio Alvorada', 'Fazenda Ouro Branco', 'Fazenda Terra Prometida'])[n],
       '98' || current_setting('app.massa_grupo') || '000000001' || lpad(n::TEXT, 2, '0'), 'Estrada Rural', (100 + n)::TEXT,
       'Zona Rural', '14' || lpad(n::TEXT, 6, '0'),
       (ARRAY['Ribeirão Preto', 'Barretos', 'Sertãozinho', 'Cravinhos', 'Bebedouro'])[(n-1)%5+1],
       'SP', 150 + n * 35, '16333344' || lpad(n::TEXT, 2, '0'),
       pg_temp.argos_id('origem_propriedade', n)
FROM generate_series(1, 15) n;

INSERT INTO user_property (user_id, property_id, link_date)
SELECT pg_temp.argos_id('usuario', u.numero), pg_temp.argos_id('propriedade', n), current_date - 200
FROM generate_series(1, 15) n
CROSS JOIN LATERAL (VALUES (n), (n+15), (32)) AS u(numero);

INSERT INTO device (id, user_id, push_token, platform, model, last_sync)
SELECT pg_temp.argos_id('dispositivo', n), pg_temp.argos_id('usuario', n),
       'token-ficticio-argos-completo-g' || current_setting('app.massa_grupo') || '-' || n::TEXT, 'ANDROID',
       (ARRAY['Moto G de teste', 'Galaxy A de teste', 'Pixel de teste'])[(n-1)%3+1],
       current_timestamp
FROM generate_series(1, 30) n;

-- 2. Rebanhos e lotes. Os 5.000 animais normais são representados por contagens.
INSERT INTO herd (id, property_id, herd_name, breed, source_id)
SELECT pg_temp.argos_id('rebanho', n), pg_temp.argos_id('propriedade', (n-1)%15+1),
       'Rebanho de homologação ' || lpad(n::TEXT, 2, '0'),
       (ARRAY['Nelore', 'Angus', 'Brahman', 'Brangus', 'Senepol'])[(n-1)%5+1],
       pg_temp.argos_id('origem_rebanho', n)
FROM generate_series(1, 30) n;

INSERT INTO lot (id, herd_id, lot_code, category, head_count, open_date, source_id)
SELECT pg_temp.argos_id('lote', n), pg_temp.argos_id('rebanho', (n-1)%30+1),
       'HC-LOTE-' || lpad(n::TEXT, 3, '0'),
       (ARRAY['CRIA', 'RECRIA', 'ENGORDA'])[(n-1)%3+1], 50, current_date - 180,
       pg_temp.argos_id('origem_lote', n)
FROM generate_series(1, 100) n;

CREATE TEMP TABLE _argos_excecoes ON COMMIT DROP AS
SELECT n, pg_temp.argos_id('animal', n) AS animal_id,
       pg_temp.argos_id('excecao', n) AS lote_id,
       pg_temp.argos_id('lote', n) AS origem_id,
       (ARRAY['Pneumonia em acompanhamento', 'Lesão no casco',
              'Baixa condição corporal', 'Ferimento por arame', 'Reação adversa'])[(n-1)%5+1] AS motivo
FROM generate_series(1, 20) n;

INSERT INTO lot (id, herd_id, parent_lot_id, lot_code, category, is_exception_lot,
                 exception_reason, head_count, open_date, source_id)
SELECT e.lote_id, l.herd_id, e.origem_id, 'HC-EXC-' || lpad(e.n::TEXT, 3, '0'),
       l.category, true, e.motivo, 0, current_date - 60,
       pg_temp.argos_id('origem_excecao', e.n)
FROM _argos_excecoes e JOIN lot l ON l.id = e.origem_id;

INSERT INTO animal_exception (id, lot_id, origin_lot_id, ear_tag, category,
                              birth_date, current_weight, exception_date,
                              exception_reason, status, source_id)
SELECT animal_id, lote_id, origem_id, 'HC-BR-' || lpad(n::TEXT, 3, '0'), 'BOI',
       current_date - (800 + n*15), 350 + n*7, current_date - 60, motivo, 'ATIVO',
       pg_temp.argos_id('origem_animal', n)
FROM _argos_excecoes;
-- O trigger tira uma cabeça do lote de origem e acrescenta uma ao sublote.

INSERT INTO animal_lot_history (id, animal_exception_id, lot_id, entry_date, reason)
SELECT pg_temp.argos_id('historico', n), animal_id, lote_id, current_date - 60, 'EXCECAO'
FROM _argos_excecoes;

-- 3. Medicamentos fictícios. As doses e carências servem apenas à homologação.
CREATE TEMP TABLE _argos_medicamentos (
    n INTEGER PRIMARY KEY, nome TEXT, principio TEXT, categoria TEXT,
    carencia INTEGER, dose NUMERIC, via TEXT
) ON COMMIT DROP;
INSERT INTO _argos_medicamentos VALUES
(1, 'Ivervet Teste', 'Ivermectina', 'Antiparasitário', 35, 10, 'SUBCUTANEA'),
(2, 'Bovivet Teste', 'Penicilina', 'Antibiótico', 30, 15, 'INTRAMUSCULAR'),
(3, 'DexaGado Teste', 'Dexametasona', 'Anti-inflamatório', 15, 5, 'INTRAMUSCULAR'),
(4, 'Carrapatox Teste', 'Cipermetrina', 'Ectoparasiticida', 14, 20, 'TOPICA'),
(5, 'Oxivet Teste', 'Oxitetraciclina', 'Antibiótico', 28, 10, 'SUBCUTANEA'),
(6, 'DoraVet Teste', 'Doramectina', 'Antiparasitário', 42, 10, 'SUBCUTANEA'),
(7, 'Flunixin Teste', 'Flunixin', 'Anti-inflamatório', 10, 8, 'INTRAMUSCULAR'),
(8, 'Ceftiofur Teste', 'Ceftiofur', 'Antibiótico', 4, 5, 'INTRAMUSCULAR'),
(9, 'SupleVet Teste', 'Vitamina B12', 'Vitamínico', 0, 10, 'INTRAMUSCULAR'),
(10, 'Complexo B Teste', 'Complexo B', 'Vitamínico', 0, 5, 'INTRAMUSCULAR'),
(11, 'Vacina Teste A', 'Antígeno fictício A', 'Vacina', 0, 2, 'SUBCUTANEA'),
(12, 'Vacina Teste B', 'Antígeno fictício B', 'Vacina', 0, 5, 'SUBCUTANEA'),
(13, 'Cefalexina Teste', 'Cefalexina', 'Antibiótico', 7, 10, 'INTRAMUSCULAR'),
(14, 'Albendazol Teste', 'Albendazol', 'Antiparasitário', 14, 15, 'TOPICA'),
(15, 'CalciumVet Teste', 'Gluconato de cálcio', 'Suplemento Mineral', 0, 50, 'INTRAVENOSA');

INSERT INTO supplier (id, company_name, cnpj, email, phone, address, source_id)
SELECT pg_temp.argos_id('fornecedor', n), 'Distribuidora Veterinária Teste ' || n::TEXT,
       '99' || current_setting('app.massa_grupo') || '000000001' || lpad(n::TEXT, 2, '0'),
       'contato' || n::TEXT || '@fornecedor.test', '163901' || lpad(n::TEXT, 4, '0'),
       'Distrito Industrial, bloco ' || n::TEXT, pg_temp.argos_id('origem_fornecedor', n)
FROM generate_series(1, 15) n;

INSERT INTO medicine (id, supplier_id, therapeutic_category_id, brand_name,
                      active_ingredient, indication, description, needs_prescription,
                      meat_withdrawal_days, dose, unit, route, source_id)
SELECT pg_temp.argos_id('medicamento', m.n), pg_temp.argos_id('fornecedor', m.n), c.id,
       m.nome, m.principio, 'Cenário fictício de ' || lower(m.categoria),
       'Produto fictício para testar estoque, aplicação e carência.',
       m.categoria = 'Antibiótico', m.carencia, m.dose, 'ml', m.via,
       pg_temp.argos_id('origem_medicamento', m.n)
FROM _argos_medicamentos m JOIN therapeutic_category c ON c.name_norm = fn_norm_texto(m.categoria);

-- 45 lotes disponíveis + um vencido, um recolhido e um esgotado.
INSERT INTO medicine_batch (id, medicine_id, batch_number, manufacture_date, expiry_date,
                            delivery_date, initial_quantity, min_stock, max_stock,
                            storage_place, status, source_id)
SELECT pg_temp.argos_id('frasco', n), pg_temp.argos_id('medicamento', (n-1)%15+1),
       'HC-MED-' || lpad(n::TEXT, 3, '0'), current_date - 600,
       CASE WHEN n = 46 THEN current_date - 10 ELSE current_date + 540 END,
       current_date - 90, CASE WHEN n <= 45 THEN 5000 ELSE 500 END,
       100, 7000, 'Galpão de teste, prateleira ' || ((n-1)%10+1)::TEXT,
       CASE n WHEN 46 THEN 'VENCIDO' WHEN 47 THEN 'RECOLHIDO'
              WHEN 48 THEN 'ESGOTADO' ELSE 'DISPONIVEL' END,
       pg_temp.argos_id('origem_frasco', n)
FROM generate_series(1, 48) n;

INSERT INTO stock_movement (id, medicine_batch_id, user_id, type, quantity, reason, movement_date)
SELECT pg_temp.argos_id('entrada', n), b.id,
       (SELECT id FROM app_user WHERE cpf = '0000000000' || (current_setting('app.massa_grupo')::INTEGER - 1)::TEXT), 'E', b.initial_quantity,
       'CARGA_INICIAL', b.delivery_date + time '08:00'
FROM generate_series(1, 48) n JOIN medicine_batch b ON b.id = pg_temp.argos_id('frasco', n);

INSERT INTO stock_movement (id, medicine_batch_id, user_id, type, quantity, reason, movement_date)
VALUES
(pg_temp.argos_id('saida_manual', 1), pg_temp.argos_id('frasco', 46),
 pg_temp.argos_id('usuario', 31), 'S', 500, 'VENCIMENTO', current_date - 10 + time '10:00'),
(pg_temp.argos_id('saida_manual', 2), pg_temp.argos_id('frasco', 48),
 pg_temp.argos_id('usuario', 31), 'S', 500, 'AJUSTE', current_timestamp);

-- 4. Aplicações. Quem aplica tem vínculo com a propriedade do lote.
INSERT INTO application (id, lot_id, medicine_batch_id, user_id, application_date,
                         application_time, dose, unit, route, head_count_applied,
                         applied_limb, applied_site, goal, notes, synced_at)
SELECT pg_temp.argos_id('aplicacao_coletiva', n), l.id, b.id,
       pg_temp.argos_id('usuario', (((n-1)%100)%30)%15+16),
       current_date - ((n-1)%30), time '07:00' + ((n-1)%10) * interval '30 minutes',
       m.dose, m.unit, m.route, 5, 'Conforme cadastro de homologação', 'Curral de manejo',
       'Tratamento coletivo fictício', 'HC aplicação coletiva ' || n::TEXT, current_timestamp
FROM generate_series(1, 260) n
JOIN lot l ON l.id = pg_temp.argos_id('lote', (n-1)%100+1)
JOIN medicine_batch b ON b.id = pg_temp.argos_id('frasco', (n-1)%45+1)
JOIN medicine m ON m.id = b.medicine_id;

-- O tratamento individual usa dose ajustada em relação ao cadastro do produto.
-- A dose efetiva fica igual em application e application_animal: a baixa automática
-- de estoque calcula application.dose * head_count_applied, como no procedimento atual.
INSERT INTO application (id, lot_id, medicine_batch_id, user_id, application_date,
                         application_time, dose, unit, route, head_count_applied,
                         applied_limb, applied_site, goal, notes, synced_at)
SELECT pg_temp.argos_id('aplicacao_individual', e.n), e.lote_id, b.id,
       pg_temp.argos_id('usuario', (e.n-1)%15+16), current_date - ((e.n-1)%15),
       time '09:00', round(m.dose * (1 + (1 + (e.n-1)%3) * 0.1), 3), m.unit, m.route, 1,
       'Conforme cadastro de homologação', 'Enfermaria', 'Tratamento individual fictício',
       'HC aplicação individual ' || e.n::TEXT, current_timestamp
FROM _argos_excecoes e
JOIN medicine_batch b ON b.id = pg_temp.argos_id('frasco', e.n)
JOIN medicine m ON m.id = b.medicine_id;

INSERT INTO application_animal (id, application_id, animal_exception_id, dose_given, weight_at_time)
SELECT pg_temp.argos_id('dose_animal', e.n), a.id, e.animal_id, a.dose, ae.current_weight
FROM _argos_excecoes e
JOIN application a ON a.id = pg_temp.argos_id('aplicacao_individual', e.n)
JOIN animal_exception ae ON ae.id = e.animal_id;
-- Carências, aptidão, 280 saídas, alertas e auditorias são produzidos pelos triggers.

-- 5. Ocorrências: oito mortes de animais diferentes, após todos os tratamentos.
-- As outras 22 ocorrências pertencem aos 12 animais que continuam ativos.
CREATE TEMP TABLE _argos_ocorrencias ON COMMIT DROP AS
SELECT n, CASE WHEN (n-1)%4 = 1 THEN (n-2)/4+1
               ELSE 9 + (n-1 - (n+2)/4)%12 END AS animal_numero,
       (ARRAY['DOENCA', 'MORTE', 'REACAO_ADVERSA', 'FERIMENTO'])[(n-1)%4+1] AS tipo
FROM generate_series(1, 30) n;

INSERT INTO occurrence (id, animal_exception_id, user_id, type, occurrence_date,
                        description, diagnosis, resolved, resolved_at)
SELECT pg_temp.argos_id('ocorrencia', o.n), e.animal_id,
       pg_temp.argos_id('usuario', (e.n-1)%15+16), o.tipo,
       CASE WHEN o.tipo = 'MORTE' THEN current_date ELSE current_date - 20 - o.n%5 END,
       'Ocorrência fictícia ' || o.n::TEXT || ': ' || lower(o.tipo),
       CASE WHEN o.tipo = 'MORTE' THEN 'Óbito confirmado no cenário de teste'
            ELSE 'Avaliação veterinária fictícia' END,
       o.tipo = 'MORTE' OR o.n%2 = 0,
       CASE WHEN o.tipo = 'MORTE' THEN current_date
            WHEN o.n%2 = 0 THEN current_date - 18 - o.n%5 END
FROM _argos_ocorrencias o JOIN _argos_excecoes e ON e.n = o.animal_numero;

UPDATE animal_exception SET status = 'MORTO', fit_for_slaughter = false,
    exception_end_date = current_date, notes = 'Óbito fictício após o tratamento; ocorrência registrada.'
WHERE id IN (SELECT animal_id FROM _argos_excecoes WHERE n <= 8);
-- O trigger reduz as cabeças e encerra o histórico na data corrente, igual à morte.
UPDATE lot SET active = false, close_date = current_date
WHERE id IN (SELECT lote_id FROM _argos_excecoes WHERE n <= 8);

-- 6. Outros alertas: só carência é gerada automaticamente pelo esquema atual.
INSERT INTO alert (id, user_id, lot_id, animal_exception_id, type, severity, title, message)
VALUES
(pg_temp.argos_id('alerta_manual', 1), pg_temp.argos_id('usuario', 31), NULL, NULL,
 'VALIDADE', 'ALTA', 'Lote HC-MED-046 vencido',
 'Cenário de teste: 500 ml retirados do estoque pelo vencimento do lote HC-MED-046.'),
(pg_temp.argos_id('alerta_manual', 2), pg_temp.argos_id('usuario', 31), NULL, NULL,
 'ESTOQUE_BAIXO', 'ALTA', 'Lote HC-MED-048 sem estoque',
 'Cenário de teste: saldo zero, abaixo do mínimo de 100 ml do lote HC-MED-048.'),
(pg_temp.argos_id('alerta_manual', 3), pg_temp.argos_id('usuario', 16),
 pg_temp.argos_id('excecao', 1), pg_temp.argos_id('animal', 1),
 'OCORRENCIA', 'ALTA', 'Óbito registrado no cenário fictício',
 'O animal HC-BR-001 foi encerrado e a contagem do lote foi atualizada.');

-- Administrador também tem dispositivo para receber os dois alertas de estoque.
INSERT INTO device (id, user_id, push_token, platform, model, last_sync)
VALUES (pg_temp.argos_id('dispositivo', 31), pg_temp.argos_id('usuario', 31),
        'token-ficticio-argos-completo-g' || current_setting('app.massa_grupo') || '-31', 'ANDROID', 'Emulador administrativo', current_timestamp);

-- Cada alerta desta massa recebe uma tentativa no dispositivo de seu destinatário.
INSERT INTO notification_delivery (id, alert_id, device_id, channel, status, attempts, sent_at, error)
SELECT pg_temp.argos_id('notificacao', a.numero::INTEGER), a.id, d.id, 'PUSH',
       CASE a.numero%3 WHEN 0 THEN 'PENDENTE' WHEN 1 THEN 'ENVIADO' ELSE 'FALHOU' END,
       CASE a.numero%3 WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE 3 END,
       CASE WHEN a.numero%3 = 1 THEN current_timestamp END,
       CASE WHEN a.numero%3 = 2 THEN 'Falha simulada de disponibilidade do provedor' END
FROM (
    SELECT al.*, row_number() OVER (ORDER BY al.id) AS numero
    FROM alert al
    WHERE al.id IN (SELECT pg_temp.argos_id('alerta_manual', n) FROM generate_series(1, 3) n)
       OR al.lot_id IN (SELECT pg_temp.argos_id('lote', n) FROM generate_series(1, 100) n)
       OR al.lot_id IN (SELECT lote_id FROM _argos_excecoes)
) a JOIN device d ON d.user_id = a.user_id
WHERE d.id IN (SELECT pg_temp.argos_id('dispositivo', n) FROM generate_series(1, 31) n);

-- 50 metadados de anexos fictícios; não representam arquivos enviados ao storage.
-- entity_id aponta para registros existentes, apesar de não haver FK polimórfica.
INSERT INTO attachment (id, user_id, entity, entity_id, type, file_path, file_hash, size_bytes)
SELECT pg_temp.argos_id('anexo', n), pg_temp.argos_id('usuario', 31),
       CASE (n-1)%5 WHEN 0 THEN 'animal_exception' WHEN 1 THEN 'medicine'
            WHEN 2 THEN 'medicine_batch' WHEN 3 THEN 'occurrence' ELSE 'application' END,
       CASE (n-1)%5
            WHEN 0 THEN pg_temp.argos_id('animal', (n-1)%20+1)
            WHEN 1 THEN pg_temp.argos_id('medicamento', (n-1)%15+1)
            WHEN 2 THEN pg_temp.argos_id('frasco', (n-1)%45+1)
            WHEN 3 THEN pg_temp.argos_id('ocorrencia', (n-1)%30+1)
            ELSE pg_temp.argos_id('aplicacao_individual', (n-1)%20+1) END,
       (ARRAY['FOTO_BRINCO', 'BULA', 'NOTA_FISCAL', 'LAUDO', 'RECEITA'])[(n-1)%5+1],
       'homologacao/argos/anexo-ficticio-' || lpad(n::TEXT, 3, '0') ||
           CASE WHEN (n-1)%5 = 0 THEN '.jpg' ELSE '.pdf' END,
       encode(digest('Conteúdo fictício do anexo ' || n::TEXT, 'sha256'), 'hex'),
       1024 + n*100
FROM generate_series(1, 50) n;

-- 7. Acessos. Ordem cronológica preserva o first_login_time criado pelo trigger.
-- 30 usuários x 14 dias x 2 sessões = 840 sucessos e 420 agregados diários.
INSERT INTO access_audit (id, user_id, login_tried, success, source, ip_address, user_agent, happened_at)
SELECT pg_temp.argos_id('acesso', (u.n-1)*28 + (d.dia-1)*2 + s.sessao),
       a.id, a.email, true, 'MOBILE', '127.0.0.1', 'Argos Android Homologação',
       current_date - d.dia + CASE s.sessao WHEN 1 THEN time '07:00' ELSE time '13:00' END
FROM generate_series(1, 30) u(n)
CROSS JOIN generate_series(1, 14) d(dia)
CROSS JOIN generate_series(1, 2) s(sessao)
JOIN app_user a ON a.id = pg_temp.argos_id('usuario', u.n)
ORDER BY d.dia DESC, s.sessao, u.n;

INSERT INTO access_audit (id, user_id, login_tried, success, failure_reason,
                          source, ip_address, user_agent, happened_at)
SELECT pg_temp.argos_id('acesso_falha', n), a.id, a.email, false, 'SENHA_ERRADA',
       'MOBILE', '127.0.0.1', 'Argos Android Homologação', current_date - 1 + time '17:00'
FROM generate_series(1, 30) n JOIN app_user a ON a.id = pg_temp.argos_id('usuario', n);

INSERT INTO access_audit (id, user_id, login_tried, success, failure_reason,
                          source, ip_address, user_agent, happened_at)
VALUES (pg_temp.argos_id('acesso_falha', 31), NULL, 'inexistente@argos.test', false,
        'USUARIO_INEXISTENTE', 'WEB', '127.0.0.1', 'Navegador de homologação',
        current_date - 1 + time '18:00');

-- 1.500 interações, associadas ao dispositivo do próprio usuário.
INSERT INTO interaction_audit (id, user_id, device_id, action_type, element_type,
                               element_id, element_name, element_description, created_at)
SELECT pg_temp.argos_id('interacao', (u.n-1)*50 + (d.dia-1)*5 + a.acao),
       pg_temp.argos_id('usuario', u.n), pg_temp.argos_id('dispositivo', u.n),
       (ARRAY['ENTRAR_TELA', 'CLIQUE', 'SCROLL', 'OCULTAR', 'SAIR_TELA'])[a.acao],
       (ARRAY['ACTIVITY', 'BOTAO', 'LISTA', 'CAMPO', 'ACTIVITY'])[a.acao],
       (ARRAY['tela_lotes', 'botao_consultar', 'lista_lotes', 'campo_filtro', 'tela_lotes'])[a.acao],
       (ARRAY['Lotes', 'Consultar lote', 'Lista de lotes', 'Filtro de lote', 'Lotes'])[a.acao],
       'Interação fictícia com a consulta de lotes',
       current_date - d.dia + time '09:00' + a.acao * interval '1 minute'
FROM generate_series(1, 30) u(n)
CROSS JOIN generate_series(1, 10) d(dia)
CROSS JOIN generate_series(1, 5) a(acao);

-- 8. RPA simulada: staging e mapeamento dos 293 cadastros criados por esta massa.
-- A simulação é feita por INSERTs comuns, mantendo app.carga=false.
INSERT INTO load_run (id, routine_name, source, start_time, status, message)
VALUES (pg_temp.argos_id('execucao', 1), 'DATALOAD_COMPLETO_ARGOS_V2_G' || current_setting('app.massa_grupo'),
        'HOMOLOGACAO_FICTICIA', current_timestamp, 'EXECUTANDO',
        'Simulação de importação com triggers ativos e uma rejeição controlada.');

CREATE TEMP TABLE _argos_origem (
    tabela TEXT, destino_id UUID, origem_id UUID, chave TEXT, conteudo JSONB
) ON COMMIT DROP;

DO $origem$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('app_user', 'usuario', 30, 'cpf'), ('property', 'propriedade', 15, 'cnpj'),
        ('herd', 'rebanho', 30, 'herd_name'), ('lot', 'lote', 100, 'lot_code'),
        ('lot', 'excecao', 20, 'lot_code'), ('animal_exception', 'animal', 20, 'ear_tag'),
        ('supplier', 'fornecedor', 15, 'cnpj'), ('medicine', 'medicamento', 15, 'brand_name'),
        ('medicine_batch', 'frasco', 48, 'batch_number')
    ) AS entidades(tabela, entidade, quantidade, chave) LOOP
        EXECUTE format(
            'INSERT INTO _argos_origem SELECT %L, t.id, t.source_id, to_jsonb(t)->>%L,
                (to_jsonb(t) - ''id'') || jsonb_build_object(''tabela'', %L)
             FROM %s t WHERE t.id IN
                (SELECT pg_temp.argos_id(%L, n) FROM generate_series(1, %s) n)',
            r.tabela, r.chave, r.tabela, to_regclass(r.tabela), r.entidade, r.quantidade);
    END LOOP;
END;
$origem$;

-- Acrescenta referências de origem no formato lido pela procedure atual.
UPDATE _argos_origem o SET conteudo = conteudo || jsonb_build_object('source_user_id', u.source_id)
FROM property p JOIN app_user u ON u.id = p.user_id
WHERE o.tabela = 'property' AND o.destino_id = p.id;
UPDATE _argos_origem o SET conteudo = conteudo || jsonb_build_object('source_property_id', p.source_id)
FROM herd h JOIN property p ON p.id = h.property_id
WHERE o.tabela = 'herd' AND o.destino_id = h.id;
UPDATE _argos_origem o SET conteudo = conteudo || jsonb_build_object('source_herd_id', h.source_id)
FROM lot l JOIN herd h ON h.id = l.herd_id
WHERE o.tabela = 'lot' AND o.destino_id = l.id;
UPDATE _argos_origem o SET conteudo = conteudo || jsonb_build_object(
    'source_lot_id', l.source_id, 'source_origin_lot_id', origem.source_id)
FROM animal_exception a JOIN lot l ON l.id = a.lot_id JOIN lot origem ON origem.id = a.origin_lot_id
WHERE o.tabela = 'animal_exception' AND o.destino_id = a.id;
UPDATE _argos_origem o SET conteudo = conteudo || jsonb_build_object(
    'source_supplier_id', s.source_id, 'therapeutic_category', c.name)
FROM medicine m JOIN supplier s ON s.id = m.supplier_id
JOIN therapeutic_category c ON c.id = m.therapeutic_category_id
WHERE o.tabela = 'medicine' AND o.destino_id = m.id;
UPDATE _argos_origem o SET conteudo = conteudo || jsonb_build_object('source_medicine_id', m.source_id)
FROM medicine_batch b JOIN medicine m ON m.id = b.medicine_id
WHERE o.tabela = 'medicine_batch' AND o.destino_id = b.id;

INSERT INTO load_mapping (table_name, source_id, target_id, natural_key)
SELECT tabela, origem_id::TEXT, destino_id, chave FROM _argos_origem;

INSERT INTO load_staging (load_run_id, source_columns, row_hash, processed)
SELECT pg_temp.argos_id('execucao', 1), conteudo::TEXT,
       encode(digest(conteudo::TEXT, 'sha256'), 'hex'), true
FROM _argos_origem;

-- A linha inválida fica SOMENTE em staging/error. A tentativa real de INSERT é
-- desfeita por um subbloco de exceção, e o CHECK do banco fornece a mensagem.
DO $rejeicao_controlada$
DECLARE
    v_original JSONB := jsonb_build_object(
        'tabela', 'medicine', 'source_id', pg_temp.argos_id('origem_rejeitada', 1),
        'source_supplier_id', pg_temp.argos_id('origem_fornecedor', 1),
        'therapeutic_category', 'Antiparasitário',
        'brand_name', 'Produto inválido de teste', 'active_ingredient', 'Ingrediente fictício',
        'indication', 'Teste de rejeição do CHECK de dose positiva',
        'needs_prescription', false, 'meat_withdrawal_days', 0,
        'dose', -1, 'unit', 'ml', 'route', 'SUBCUTANEA');
    v_erro TEXT;
BEGIN
    INSERT INTO load_staging (id, load_run_id, source_columns, row_hash, processed)
    VALUES (pg_temp.argos_id('staging_rejeitado', 1), pg_temp.argos_id('execucao', 1),
            v_original::TEXT, encode(digest(v_original::TEXT, 'sha256'), 'hex'), false);
    BEGIN
        INSERT INTO medicine (id, supplier_id, therapeutic_category_id, brand_name,
                              active_ingredient, indication, dose, unit, route)
        SELECT pg_temp.argos_id('medicamento_rejeitado', 1), pg_temp.argos_id('fornecedor', 1),
               therapeutic_category_id, v_original->>'brand_name', v_original->>'active_ingredient',
               v_original->>'indication', (v_original->>'dose')::NUMERIC,
               v_original->>'unit', v_original->>'route'
        FROM medicine WHERE id = pg_temp.argos_id('medicamento', 1);
    EXCEPTION WHEN check_violation THEN
        GET STACKED DIAGNOSTICS v_erro = MESSAGE_TEXT;
    END;
    IF v_erro IS NULL THEN
        RAISE EXCEPTION 'O banco aceitou dose negativa; a regra esperada está ausente.';
    END IF;
    INSERT INTO load_error (id, load_run_id, source_table, natural_key, original_content,
                            error_message, attempts, reprocessed)
    VALUES (pg_temp.argos_id('erro', 1), pg_temp.argos_id('execucao', 1), 'medicine',
            'Produto inválido de teste', v_original, v_erro, 1, false);
END;
$rejeicao_controlada$;

UPDATE load_run SET end_time = current_timestamp,
    rows_read = (SELECT count(*) FROM load_staging WHERE load_run_id = pg_temp.argos_id('execucao', 1)),
    rows_inserted = (SELECT count(*) FROM _argos_origem), rows_updated = 0,
    rows_rejected = (SELECT count(*) FROM load_error WHERE load_run_id = pg_temp.argos_id('execucao', 1)),
    status = 'PARCIAL', loaded_until = current_timestamp,
    message = '293 cadastros simulados; uma dose negativa rejeitada pelo CHECK, disponível para reprocessamento.'
WHERE id = pg_temp.argos_id('execucao', 1);

-- 9. Catálogo: todas as colunas existentes das 30 tabelas, inclusive as de BI.
-- A classificação e a base legal abaixo são exemplos fictícios de homologação.
-- As duas séries compartilham uma única entrada por coluna do catálogo.
INSERT INTO data_catalog (table_name, column_name, data_type, description,
                          business_rule, access_level, personal_data, lgpd_legal_basis)
SELECT c.table_name, c.column_name, c.data_type,
       r.descricao || ': ' || c.column_name || '.',
       concat_ws(' ',
           CASE WHEN c.is_nullable = 'NO' THEN 'Obrigatório.' ELSE 'Aceita nulo.' END,
           CASE WHEN c.is_generated = 'ALWAYS' THEN 'Calculado automaticamente pelo banco.'
                WHEN c.column_default IS NOT NULL THEN 'Padrão: ' || c.column_default || '.' END,
           (SELECT string_agg(pg_get_constraintdef(co.oid), '; ' ORDER BY co.conname)
            FROM pg_constraint co JOIN pg_attribute at ON at.attrelid = co.conrelid
            WHERE co.conrelid = to_regclass(c.table_name)
              AND at.attname = c.column_name AND at.attnum = ANY(co.conkey))),
       CASE WHEN c.column_name IN ('cpf', 'firebase_uid', 'push_token') THEN 'SENSIVEL'
            WHEN c.column_name IN ('name', 'email', 'phone', 'street', 'number',
                                  'complement', 'zip_code', 'photo_url', 'ip_address',
                                  'user_agent', 'login_tried', 'address', 'address_full') THEN 'RESTRITO'
            ELSE 'INTERNO' END,
       c.column_name IN ('cpf', 'firebase_uid', 'push_token', 'name', 'email', 'phone',
           'street', 'number', 'complement', 'zip_code', 'photo_url', 'ip_address',
           'user_agent', 'login_tried', 'address', 'address_full'),
       CASE WHEN c.column_name IN ('cpf', 'firebase_uid', 'push_token', 'name', 'email',
           'phone', 'street', 'number', 'complement', 'zip_code', 'photo_url', 'ip_address',
           'user_agent', 'login_tried', 'address', 'address_full')
            THEN 'EXEMPLO FICTICIO PARA HOMOLOGACAO' END
FROM information_schema.columns c JOIN _argos_resumo r ON r.tabela = c.table_name
WHERE c.table_schema = (SELECT n.nspname FROM pg_class cl JOIN pg_namespace n ON n.oid = cl.relnamespace
                       WHERE cl.oid = 'app_user'::regclass)
ON CONFLICT (table_name, column_name) DO NOTHING;

-- 10. Calendário do BI: duas faixas contíguas, dobrando a cobertura original.
INSERT INTO dim_date (date_key, date, day, month, month_name, quarter, year, week, is_weekend)
SELECT to_char(d, 'YYYYMMDD')::INTEGER, d::DATE, extract(day FROM d)::INTEGER,
       extract(month FROM d)::INTEGER,
       (ARRAY['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
              'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'])[extract(month FROM d)::INTEGER],
       extract(quarter FROM d)::INTEGER, extract(year FROM d)::INTEGER,
       extract(week FROM d)::INTEGER, extract(isodow FROM d) IN (6, 7)
FROM (
    SELECT least(date '2024-01-01', current_date - 1200) AS inicio,
           greatest(date '2030-12-31', current_date + 730) AS fim
) calendario
CROSS JOIN LATERAL generate_series(
    (calendario.inicio - (current_setting('app.massa_grupo')::INTEGER - 1)
        * (calendario.fim - calendario.inicio + 1))::TIMESTAMP,
    (calendario.fim - (current_setting('app.massa_grupo')::INTEGER - 1)
        * (calendario.fim - calendario.inicio + 1))::TIMESTAMP, interval '1 day') d
ON CONFLICT (date_key) DO NOTHING;

-- 11. Conferências reais, antes do COMMIT. Falhas impedem confirmar a massa.
DO $validacao$
DECLARE r RECORD; v_total BIGINT;
BEGIN
    IF fn_em_carga() THEN RAISE EXCEPTION 'app.carga foi ativado durante a carga.'; END IF;

    FOR r IN SELECT tabela FROM _argos_resumo LOOP
        EXECUTE format('SELECT count(*) FROM %s', to_regclass(r.tabela)) INTO v_total;
        IF v_total = 0 THEN RAISE EXCEPTION 'A tabela % ficou vazia.', r.tabela; END IF;
        UPDATE _argos_resumo SET depois = v_total WHERE tabela = r.tabela;
    END LOOP;

    IF (SELECT count(*) FROM application WHERE id IN (
        SELECT pg_temp.argos_id('aplicacao_coletiva', n) FROM generate_series(1, 260) n
        UNION ALL SELECT pg_temp.argos_id('aplicacao_individual', n) FROM generate_series(1, 20) n)) <> 280 THEN
        RAISE EXCEPTION 'Quantidade de aplicações diferente das 280 previstas.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM application a JOIN lot l ON l.id = a.lot_id
        JOIN herd h ON h.id = l.herd_id JOIN medicine_batch b ON b.id = a.medicine_batch_id
        JOIN medicine m ON m.id = b.medicine_id
        WHERE a.notes LIKE 'HC aplicação %' AND (
            a.meat_withdrawal_end IS DISTINCT FROM a.application_date + m.meat_withdrawal_days
            OR a.route <> m.route OR a.unit <> m.unit
            OR a.application_date < l.open_date
            OR NOT EXISTS (SELECT 1 FROM user_property up
                           WHERE up.user_id = a.user_id AND up.property_id = h.property_id AND up.active)
            OR NOT EXISTS (SELECT 1 FROM stock_movement sm WHERE sm.application_id = a.id
                           AND sm.type = 'S' AND sm.reason = 'APLICACAO'
                           AND sm.medicine_batch_id = a.medicine_batch_id
                           AND sm.quantity = a.dose * a.head_count_applied)
            OR NOT EXISTS (SELECT 1 FROM change_audit ca WHERE ca.table_name = 'application'
                           AND ca.record_id = a.id::TEXT AND ca.operation = 'INSERT')
        )
    ) THEN RAISE EXCEPTION 'Aplicação sem carência, baixa, auditoria, via ou vínculo corretos.'; END IF;

    IF EXISTS (
        SELECT 1 FROM application_animal aa JOIN application a ON a.id = aa.application_id
        JOIN _argos_excecoes e ON e.animal_id = aa.animal_exception_id
        WHERE aa.dose_given <> a.dose
           OR aa.meat_withdrawal_end IS DISTINCT FROM a.meat_withdrawal_end
           OR aa.released_for_slaughter IS DISTINCT FROM (aa.meat_withdrawal_end <= current_date)
    ) THEN RAISE EXCEPTION 'Dose ou carência individual inconsistente.'; END IF;

    IF EXISTS (SELECT 1 FROM generate_series(1, 48) n
               WHERE fn_saldo_medicine_batch(pg_temp.argos_id('frasco', n)) < 0) THEN
        RAISE EXCEPTION 'Saldo de estoque negativo.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stock_movement sm
        WHERE sm.medicine_batch_id IN (SELECT pg_temp.argos_id('frasco', n) FROM generate_series(1, 48) n)
          AND NOT EXISTS (SELECT 1 FROM change_audit ca WHERE ca.table_name = 'stock_movement'
                          AND ca.record_id = sm.id::TEXT AND ca.operation = 'INSERT')
    ) THEN RAISE EXCEPTION 'Movimento de estoque sem auditoria automática.'; END IF;

    IF (SELECT sum(head_count) FROM lot WHERE id IN (
        SELECT pg_temp.argos_id('lote', n) FROM generate_series(1, 100) n
        UNION ALL SELECT lote_id FROM _argos_excecoes)) <> 4992 THEN
        RAISE EXCEPTION 'Contagem de animais diferente das 4.992 cabeças vivas previstas.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM herd h WHERE h.id IN (SELECT pg_temp.argos_id('rebanho', n) FROM generate_series(1, 30) n)
        AND h.head_count <> (SELECT coalesce(sum(l.head_count), 0) FROM lot l WHERE l.herd_id = h.id AND l.active)
    ) THEN RAISE EXCEPTION 'Contagem do rebanho diferente da soma dos lotes ativos.'; END IF;

    IF EXISTS (
        SELECT 1 FROM _argos_excecoes e JOIN animal_exception a ON a.id = e.animal_id
        JOIN lot l ON l.id = e.lote_id JOIN animal_lot_history hist ON hist.animal_exception_id = a.id
        WHERE (e.n <= 8 AND (a.status <> 'MORTO' OR a.fit_for_slaughter
               OR l.head_count <> 0 OR l.active OR hist.exit_date IS DISTINCT FROM current_date))
           OR (e.n > 8 AND (a.status <> 'ATIVO' OR l.head_count <> 1 OR hist.exit_date IS NOT NULL))
    ) THEN RAISE EXCEPTION 'Estado ou contagem dos animais de exceção inconsistente.'; END IF;
    IF (SELECT count(DISTINCT animal_exception_id) FROM occurrence
        WHERE id IN (SELECT pg_temp.argos_id('ocorrencia', n) FROM generate_series(1, 30) n)
          AND type = 'MORTE') <> 8 THEN
        RAISE EXCEPTION 'As oito mortes precisam pertencer a oito animais diferentes.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM application a JOIN _argos_excecoes e ON e.lote_id = a.lot_id
        JOIN occurrence o ON o.animal_exception_id = e.animal_id AND o.type = 'MORTE'
        WHERE a.application_date > o.occurrence_date
    ) THEN RAISE EXCEPTION 'Foi encontrada aplicação posterior à morte.'; END IF;

    IF (SELECT count(*) FROM daily_access WHERE user_id IN (
        SELECT pg_temp.argos_id('usuario', n) FROM generate_series(1, 30) n)) <> 420
       OR EXISTS (SELECT 1 FROM daily_access WHERE user_id IN (
            SELECT pg_temp.argos_id('usuario', n) FROM generate_series(1, 30) n) AND session_count <> 2) THEN
        RAISE EXCEPTION 'Os acessos bem-sucedidos não produziram os 420 agregados esperados.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM load_run WHERE id = pg_temp.argos_id('execucao', 1)
        AND rows_read = 294 AND rows_inserted = 293 AND rows_updated = 0
        AND rows_rejected = 1 AND status = 'PARCIAL') THEN
        RAISE EXCEPTION 'Os totais da simulação de importação estão inconsistentes.';
    END IF;
    IF EXISTS (SELECT 1 FROM medicine WHERE id = pg_temp.argos_id('medicamento_rejeitado', 1)) THEN
        RAISE EXCEPTION 'A linha inválida foi persistida no cadastro.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns c JOIN _argos_resumo resumo ON resumo.tabela = c.table_name
        WHERE c.table_schema = (SELECT n.nspname FROM pg_class cl JOIN pg_namespace n ON n.oid = cl.relnamespace
                               WHERE cl.oid = 'app_user'::regclass)
          AND NOT EXISTS (SELECT 1 FROM data_catalog dc
                          WHERE dc.table_name = c.table_name AND dc.column_name = c.column_name)
    ) THEN RAISE EXCEPTION 'Uma coluna do esquema atual ficou sem entrada no catálogo.'; END IF;

    IF EXISTS (
        SELECT 1 FROM lot l WHERE l.id IN (
            SELECT pg_temp.argos_id('lote', n) FROM generate_series(1, 100) n
            UNION ALL SELECT lote_id FROM _argos_excecoes)
        AND (l.slaughter_release_date IS DISTINCT FROM
                (SELECT max(a.meat_withdrawal_end) FROM application a WHERE a.lot_id = l.id)
             OR l.fit_for_slaughter IS DISTINCT FROM
                (l.slaughter_release_date IS NULL OR l.slaughter_release_date <= current_date))
    ) THEN RAISE EXCEPTION 'A liberação do lote não corresponde à maior carência registrada.'; END IF;

    IF EXISTS (SELECT 1 FROM _argos_resumo resumo
               JOIN pg_trigger t ON t.tgrelid = to_regclass(resumo.tabela)
               WHERE t.tgenabled NOT IN ('O', 'A')) THEN
        RAISE EXCEPTION 'Foi encontrado um trigger inativo no esquema.';
    END IF;
END;
$validacao$;


        $inserts_da_serie$;
        DROP TABLE _argos_excecoes, _argos_medicamentos, _argos_ocorrencias, _argos_origem;
    END LOOP;
END;
$massa_em_dobro$;

-- Conferência global após as duas séries: nenhuma tabela ficou com dados antigos.
DO $validacao_global$
DECLARE r RECORD; v_total BIGINT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('permission', 5), ('therapeutic_category', 11), ('app_user', 66),
        ('property', 30), ('user_property', 90), ('device', 62), ('herd', 60),
        ('lot', 240), ('animal_exception', 40), ('supplier', 30), ('medicine', 30),
        ('medicine_batch', 96), ('application', 560), ('application_animal', 40),
        ('stock_movement', 660), ('occurrence', 60), ('animal_lot_history', 40),
        ('attachment', 100), ('change_audit', 1220), ('access_audit', 1742),
        ('interaction_audit', 3000), ('daily_access', 840), ('load_run', 2),
        ('load_error', 2), ('load_mapping', 586), ('load_staging', 588)
    ) AS esperado(tabela, quantidade) LOOP
        EXECUTE format('SELECT count(*) FROM %s', to_regclass(r.tabela)) INTO v_total;
        IF v_total <> r.quantidade THEN
            RAISE EXCEPTION 'Tabela %: esperado %, encontrado %.', r.tabela, r.quantidade, v_total;
        END IF;
    END LOOP;
    IF (SELECT sum(head_count) FROM herd) <> 9984 THEN
        RAISE EXCEPTION 'Esperadas 9.984 cabeças vivas após as 16 mortes.';
    END IF;
    IF (SELECT count(*) FROM alert) <>
       (SELECT count(*) + 6 FROM application WHERE meat_withdrawal_end > current_date)
       OR (SELECT count(*) FROM notification_delivery) <> (SELECT count(*) FROM alert) THEN
        RAISE EXCEPTION 'Quantidade de alertas ou entregas diferente da massa prevista.';
    END IF;
    IF (SELECT count(*) FROM dim_date) <>
       2 * (greatest(date '2030-12-31', current_date + 730)
               - least(date '2024-01-01', current_date - 1200) + 1) THEN
        RAISE EXCEPTION 'O calendário não contém o dobro da cobertura original.';
    END IF;
END;
$validacao_global$;

-- Resultado: registros removidos e quantidade real da nova massa por tabela.
SELECT tabela, antes AS registros_removidos, depois AS registros_novos
FROM _argos_resumo ORDER BY tabela;
SELECT sum(antes) AS total_registros_removidos, sum(depois) AS total_registros_novos,
       count(*) FILTER (WHERE depois > 0) AS tabelas_preenchidas
FROM _argos_resumo;
SELECT status, rows_read, rows_inserted, rows_updated, rows_rejected, message
FROM load_run ORDER BY routine_name;

DROP FUNCTION pg_temp.argos_id(TEXT, INTEGER);
COMMIT;
