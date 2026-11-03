

insert into permission (code, name, description)
values ('ADMIN', 'Administrador', 'Acesso total, sem restricao de escopo'),
       ('GERENTE', 'Gerente', 'Gerencia as fazendas vinculadas'),
       ('PECUARISTA', 'Pecuarista', 'Dono da fazenda'),
       ('APLICADOR', 'Aplicador', 'Registra aplicacao no campo'),
       ('SISTEMA', 'Sistema', 'Usuario tecnico da RPA, nao faz login')
on conflict (code) do nothing;

insert into therapeutic_category (name, description)
values ('Antibiótico', 'Combate infeccao bacteriana'),
       ('Anti-inflamatório', 'Reduz inflamacao, dor e febre'),
       ('Antiparasitário', 'Endo e ectoparasitas'),
       ('Vacina', 'Imunizacao preventiva'),
       ('Vitamínico', 'Suplementacao vitaminica e mineral'),
       ('Hormonal', 'Protocolo reprodutivo'),
       ('Anestésico', 'Sedacao e anestesia'),
       ('Cicatrizante', 'Tratamento topico de ferimento'),
       ('Ectoparasiticida', 'Carrapato, mosca e outros ectoparasitas'),
       ('Suplemento Mineral', 'Reposicao de minerais'),
       ('Não informado', 'Fallback da carga: grafia de origem nao reconhecida, revisar')
on conflict (name) do nothing;

insert into app_user (permission_id, name, cpf, email, firebase_uid, first_login)
select p.id, 'RPA Argos', '00000000000', 'rpa@argos.local', 'rpa-sistema', false
from permission p
where p.code = 'SISTEMA'
on conflict (cpf) do nothing;

