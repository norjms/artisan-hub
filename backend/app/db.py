from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from .config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = settings().database_url
        kw = {"pool_pre_ping": True, "future": True}
        if url.startswith("postgresql"):
            kw.update(pool_size=10, max_overflow=10)
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def SessionLocal() -> Session:
    engine()
    return _Session()


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# Lightweight forward-only migrations: (version, sql). create_all handles new tables;
# use this list for ALTERs on existing tables.
MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE job ADD COLUMN IF NOT EXISTS priority integer DEFAULT 5; "
        "UPDATE job SET priority = 8 WHERE kind = 'import.folder'; UPDATE job SET priority = 7 WHERE kind = 'dam.sync_model'"),
    (2, "ALTER TABLE model ADD COLUMN IF NOT EXISTS license_code varchar(30), ADD COLUMN IF NOT EXISTS license_checked_at timestamptz, "
        "ADD COLUMN IF NOT EXISTS credit_override text, ADD COLUMN IF NOT EXISTS makerworld_id integer, "
        "ADD COLUMN IF NOT EXISTS makerworld_instance_id integer; "
        "CREATE INDEX IF NOT EXISTS ix_model_makerworld_id ON model (makerworld_id); "
        "ALTER TABLE designer ADD COLUMN IF NOT EXISTS handle varchar(120), ADD COLUMN IF NOT EXISTS platform varchar(40), "
        "ADD COLUMN IF NOT EXISTS credit_template text, ADD COLUMN IF NOT EXISTS always_credit boolean DEFAULT false, "
        "ADD COLUMN IF NOT EXISTS notes text; "
        "UPDATE designer SET always_credit = false WHERE always_credit IS NULL; "
        "UPDATE model SET license_code = 'COMMERCIAL' WHERE source = 'deltaprints' AND license_code IS NULL; "
        "UPDATE model SET license_code = 'OWN' WHERE source = 'own' AND license_code IS NULL; "
        "UPDATE model SET license_code = CASE WHEN license_ok THEN 'COMMERCIAL' ELSE 'UNKNOWN' END WHERE license_code IS NULL"),
    (3, "ALTER TABLE model ADD COLUMN IF NOT EXISTS source_platform varchar(30), ADD COLUMN IF NOT EXISTS source_ext_id varchar(80); "
        "CREATE INDEX IF NOT EXISTS ix_model_source_ext_id ON model (source_ext_id); "
        "UPDATE model SET source_platform = 'makerworld', source_ext_id = makerworld_id::text WHERE makerworld_id IS NOT NULL"),
    (4, "INSERT INTO team (id, slug, name, personal, storage_prefix, source_dirs, created_at) VALUES (1, 'my-workshop', 'My workshop', false, '', '{}', now()) ON CONFLICT (id) DO NOTHING; SELECT setval(pg_get_serial_sequence('team', 'id'), (SELECT max(id) FROM team)); ALTER TABLE category ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE category SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE category ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_category_team_id ON category (team_id); ALTER TABLE designer ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE designer SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE designer ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_designer_team_id ON designer (team_id); ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE printer_profile SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE printer_profile ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_printer_profile_team_id ON printer_profile (team_id); ALTER TABLE license_grant ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE license_grant SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE license_grant ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_license_grant_team_id ON license_grant (team_id); ALTER TABLE model ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE model SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE model ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_model_team_id ON model (team_id); ALTER TABLE image ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE image SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE image ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_image_team_id ON image (team_id); ALTER TABLE license_evidence ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE license_evidence SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE license_evidence ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_license_evidence_team_id ON license_evidence (team_id); ALTER TABLE model_file ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE model_file SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE model_file ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_model_file_team_id ON model_file (team_id); ALTER TABLE product ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE product SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE product ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_product_team_id ON product (team_id); ALTER TABLE variant ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE variant SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE variant ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_variant_team_id ON variant (team_id); ALTER TABLE bom_filament ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE bom_filament SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE bom_filament ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_bom_filament_team_id ON bom_filament (team_id); ALTER TABLE bom_part ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE bom_part SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE bom_part ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_bom_part_team_id ON bom_part (team_id); ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE shop_order SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE shop_order ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_shop_order_team_id ON shop_order (team_id); ALTER TABLE shop_order_line ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE shop_order_line SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE shop_order_line ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_shop_order_line_team_id ON shop_order_line (team_id); ALTER TABLE job ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE job SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE job ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_job_team_id ON job (team_id); ALTER TABLE event ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE event SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE event ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_event_team_id ON event (team_id); ALTER TABLE category DROP CONSTRAINT IF EXISTS category_name_key; ALTER TABLE category ADD CONSTRAINT uq_category_team_name UNIQUE (team_id, name); ALTER TABLE category DROP CONSTRAINT IF EXISTS category_slug_key; ALTER TABLE category ADD CONSTRAINT uq_category_team_slug UNIQUE (team_id, slug); ALTER TABLE designer DROP CONSTRAINT IF EXISTS designer_name_key; ALTER TABLE designer ADD CONSTRAINT uq_designer_team_name UNIQUE (team_id, name); ALTER TABLE designer DROP CONSTRAINT IF EXISTS designer_slug_key; ALTER TABLE designer ADD CONSTRAINT uq_designer_team_slug UNIQUE (team_id, slug); ALTER TABLE printer_profile DROP CONSTRAINT IF EXISTS printer_profile_name_key; ALTER TABLE printer_profile ADD CONSTRAINT uq_printer_profile_team_name UNIQUE (team_id, name); ALTER TABLE model DROP CONSTRAINT IF EXISTS model_code_key; ALTER TABLE model ADD CONSTRAINT uq_model_team_code UNIQUE (team_id, code); ALTER TABLE model DROP CONSTRAINT IF EXISTS model_slug_key; ALTER TABLE model ADD CONSTRAINT uq_model_team_slug UNIQUE (team_id, slug); ALTER TABLE product DROP CONSTRAINT IF EXISTS product_sku_key; ALTER TABLE product ADD CONSTRAINT uq_product_team_sku UNIQUE (team_id, sku); ALTER TABLE variant DROP CONSTRAINT IF EXISTS variant_sku_key; ALTER TABLE variant ADD CONSTRAINT uq_variant_team_sku UNIQUE (team_id, sku); ALTER TABLE shop_order DROP CONSTRAINT IF EXISTS shop_order_woo_order_id_key; ALTER TABLE shop_order ADD CONSTRAINT uq_shop_order_team_woo UNIQUE (team_id, woo_order_id); CREATE INDEX IF NOT EXISTS ix_job_team_state ON job (team_id, state); ALTER TABLE setting ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE setting SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE setting ALTER COLUMN team_id SET NOT NULL; ALTER TABLE setting DROP CONSTRAINT IF EXISTS setting_pkey; ALTER TABLE setting ADD COLUMN IF NOT EXISTS id serial PRIMARY KEY; ALTER TABLE setting ADD CONSTRAINT uq_setting_team_key UNIQUE (team_id, key); CREATE INDEX IF NOT EXISTS ix_setting_team_id ON setting (team_id); ALTER TABLE api_key ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; ALTER TABLE api_key ADD COLUMN IF NOT EXISTS user_id integer REFERENCES hub_user(id) ON DELETE SET NULL; UPDATE api_key SET team_id = 1 WHERE team_id IS NULL; CREATE INDEX IF NOT EXISTS ix_api_key_team_id ON api_key (team_id); ALTER TABLE oauth_token ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; ALTER TABLE oauth_token ADD COLUMN IF NOT EXISTS user_id integer REFERENCES hub_user(id) ON DELETE CASCADE; UPDATE oauth_token SET revoked = true WHERE team_id IS NULL; CREATE INDEX IF NOT EXISTS ix_oauth_token_team_id ON oauth_token (team_id); DROP TABLE IF EXISTS part_cache; DROP TABLE IF EXISTS spool_cache; DROP TABLE IF EXISTS filament_catalog"),
    (5, "ALTER TABLE membership ADD COLUMN IF NOT EXISTS perms json; ALTER TABLE invite ALTER COLUMN team_id DROP NOT NULL; ALTER TABLE invite ADD COLUMN IF NOT EXISTS kind varchar(12) DEFAULT 'join'; ALTER TABLE invite ADD COLUMN IF NOT EXISTS team_name varchar(120); ALTER TABLE invite ADD COLUMN IF NOT EXISTS name varchar(200); ALTER TABLE invite ADD COLUMN IF NOT EXISTS perms json; ALTER TABLE invite ADD COLUMN IF NOT EXISTS ak_invitation varchar(64); ALTER TABLE invite ADD COLUMN IF NOT EXISTS ak_url varchar(500); UPDATE invite SET kind = 'join' WHERE kind IS NULL"),
    (6, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS engine varchar(10) DEFAULT 'orca'; "
        "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS machine_config text; "
        "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS tools json DEFAULT '[]'; "
        "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS dispatch varchar(20) DEFAULT 'none'; "
        "UPDATE printer_profile SET engine = 'orca' WHERE engine IS NULL; "
        "UPDATE printer_profile SET dispatch = CASE WHEN wt_printer IS NOT NULL THEN 'watchtower' ELSE 'none' END; "
        "ALTER TABLE model ADD COLUMN IF NOT EXISTS colors_needed integer; "
        "ALTER TABLE model ADD COLUMN IF NOT EXISTS inspect json DEFAULT '{}'"),
    (7, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS hourly_rate double precision"),
    (8, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS bed_type varchar(40)"),
    (9, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS filament_slots integer"),
    # people with hand-picked permissions who can slice also get the new farm permission (plate checks, queue)
    (10, "UPDATE membership SET perms = (perms::jsonb || '[\"farm\"]'::jsonb)::json WHERE perms IS NOT NULL "
         "AND perms::jsonb ? 'slice' AND NOT perms::jsonb ? 'farm'"),
    (11, "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS routed_at timestamptz; "
         "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS printed_at timestamptz; "
         "ALTER TABLE shop_order_line ADD COLUMN IF NOT EXISTS wt_job_ids json DEFAULT '[]'; "
         "ALTER TABLE shop_order_line ADD COLUMN IF NOT EXISTS wt_error text; "
         "ALTER TABLE plate_check ADD COLUMN IF NOT EXISTS ready_due timestamptz; "
         "ALTER TABLE plate_check ADD COLUMN IF NOT EXISTS photo varchar(200)"),
    (12, "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS due_at timestamptz; "
         "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS late_alerted_at timestamptz"),
    (13, "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS wt_schedule_id integer; "
         "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS wt_status varchar(20); "
         "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS wt_hours_since double precision; "
         "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS category varchar(40); "
         "ALTER TABLE maint_task ADD COLUMN IF NOT EXISTS out_of_service boolean DEFAULT false; "
         "ALTER TABLE maint_task ADD COLUMN IF NOT EXISTS wt_note text"),
    (14, "ALTER TABLE hub_user ADD COLUMN IF NOT EXISTS prefs json"),
    (15, "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS fil_plan json; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS spools json; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS used_g double precision; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS used_source varchar(12)"),
    (16, "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS expires_at timestamptz; "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS instance_id varchar(120); "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS bind_instance boolean DEFAULT false; "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS note varchar(300); "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS last_ip varchar(64)"),
    (17, "ALTER TABLE model_file ADD COLUMN IF NOT EXISTS farm_kind varchar(20); "
         "UPDATE model_file SET farm_kind = 'watchtower' WHERE watchtower_uuid IS NOT NULL AND farm_kind IS NULL"),
    # plate-aware reslicing: build_plate and plate_slice are new tables (create_all)
    (18, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS installed_plate_id integer "
         "REFERENCES build_plate(id) ON DELETE SET NULL; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS plate_reslice json"),
    (19, "ALTER TABLE build_plate ADD COLUMN IF NOT EXISTS maker varchar(60); "
         "ALTER TABLE build_plate ADD COLUMN IF NOT EXISTS surface varchar(40)"),
    # sliced files chosen by hand for a printer, and slices locked against re-slicing
    (20, "ALTER TABLE slice_result ADD COLUMN IF NOT EXISTS locked boolean DEFAULT false; "
         "ALTER TABLE slice_result ADD COLUMN IF NOT EXISTS source varchar(12)"),
    # password links (password_link is a new table, create_all) and the key setup minted
    (21, "ALTER TABLE installation ADD COLUMN IF NOT EXISTS installer_key_id integer"),
    (22, "ALTER TABLE membership ADD COLUMN IF NOT EXISTS printers json"),
]

# Data migrations written in Python, so they run the same on Postgres and in the tests' SQLite. Numbered from 1001
# so they never take a number the SQL list above may use next.
THEME_MIGRATION = 1001     # every saved skin and light/dark mode becomes the glass-dark theme (services.themes)


def init_db():
    from . import models  # noqa: F401  (register tables)
    eng = engine()
    pg = eng.dialect.name == "postgresql"
    with eng.begin() as c:
        if pg:   # web workers and the job worker start together; serialize schema work
            c.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        fresh = not eng.dialect.has_table(c, "job")
        Base.metadata.create_all(c)
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_version (v integer primary key)"))
        done = {r[0] for r in c.execute(text("SELECT v FROM schema_version"))}
        for v, sql in MIGRATIONS:
            if v in done:
                continue
            if pg and not fresh:
                for stmt in [x for x in sql.split(";") if x.strip()]:
                    c.execute(text(stmt))
            c.execute(text("INSERT INTO schema_version (v) VALUES (:v)"), {"v": v})
        Base.metadata.create_all(c)      # tables a migration rebuilt
        if THEME_MIGRATION not in done:
            from .services.themes import migrate_prefs
            migrate_prefs(c)
            c.execute(text("INSERT INTO schema_version (v) VALUES (:v)"), {"v": THEME_MIGRATION})
        from .services.teams import bootstrap
        bootstrap(c, fresh)
