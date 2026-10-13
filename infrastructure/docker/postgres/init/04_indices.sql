-- indices


create index idx_application_carencia
    on application (meat_withdrawal_end)
    where meat_withdrawal_end is not null;


create unique index idx_animal_exception_lot_ear_tag
    on animal_exception (lot_id, ear_tag)
    where status = 'ATIVO';


create index idx_change_audit_tabela_data
    on change_audit (table_name, happened_at);


create index idx_stock_movement_lote_data
    on stock_movement (medicine_batch_id, movement_date);

create index idx_application_lot_data on application (lot_id, application_date);
create index idx_lot_parent on lot (parent_lot_id);
create index idx_interaction_user_data on interaction_audit (user_id, created_at);
create index idx_access_audit_user_data on access_audit (user_id, happened_at);
create index idx_access_audit_login_data on access_audit (login_tried, happened_at);

create index idx_property_user on property (user_id);
create index idx_herd_property on herd (property_id);
create index idx_application_batch on application (medicine_batch_id);
create index idx_animal_exception_origin on animal_exception (origin_lot_id);
create index idx_medicine_supplier on medicine (supplier_id);
create index idx_medicine_categoria on medicine (therapeutic_category_id);
create index idx_medicine_batch_medicine on medicine_batch (medicine_id);

create unique index uq_app_user_firebase on app_user (firebase_uid);
create unique index uq_app_user_source on app_user (source_id) where source_id is not null;
create unique index uq_property_source on property (source_id) where source_id is not null;
create unique index uq_herd_source on herd (source_id) where source_id is not null;
create unique index uq_lot_source on lot (source_id) where source_id is not null;
create unique index uq_animal_exception_source on animal_exception (source_id) where source_id is not null;
create unique index uq_supplier_source on supplier (source_id) where source_id is not null;
create unique index uq_medicine_source on medicine (source_id) where source_id is not null;
create unique index uq_medicine_batch_source on medicine_batch (source_id) where source_id is not null;
create unique index uq_application_source on application (source_id) where source_id is not null;
create unique index uq_stock_movement_source on stock_movement (source_id) where source_id is not null;
create unique index uq_stock_movement_application on stock_movement (application_id) where application_id is not null;
create unique index uq_animal_lot_history_ativo
    on animal_lot_history (animal_exception_id)
    where exit_date is null;

