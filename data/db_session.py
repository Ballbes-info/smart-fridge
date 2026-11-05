import sqlalchemy as sa
import sqlalchemy.orm as orm
from sqlalchemy.orm import Session

SqlAlchemyBase = orm.declarative_base()

__factory = None


def global_init(db_file: str) -> None:
    global __factory

    if __factory:
        return

    if not db_file or not db_file.strip():
        raise ValueError("Не указан файл базы данных.")

    conn_str = f"sqlite:///{db_file.strip()}?check_same_thread=False"
    print(f"Подключение к базе данных по адресу {conn_str}")

    engine = sa.create_engine(conn_str, echo=False)
    @sa.event.listens_for(engine, 'connect')
    def install_casefold(connection, record):
        connection.create_function('unicode_casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)

    __factory = orm.sessionmaker(bind=engine)

    from . import __all_models

    SqlAlchemyBase.metadata.create_all(engine)
    # Additive migration for account databases created before session invalidation.
    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        columns={row[1] for row in conn.exec_driver_sql('PRAGMA table_info(users)')}
        if 'auth_version' not in columns:
            conn.exec_driver_sql('ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0')
    # Old family events had no scope. Keep legacy details visible to their actor only.
    with engine.begin() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        family_columns={row[1] for row in conn.exec_driver_sql('PRAGMA table_info(family_events)')}
        if 'storage_id' not in family_columns:
            conn.exec_driver_sql('ALTER TABLE family_events ADD COLUMN storage_id INTEGER')
            conn.exec_driver_sql('UPDATE family_events SET storage_id=0')
        conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_family_events_storage_id ON family_events(storage_id)')
    with engine.begin() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        shopping_columns={row[1] for row in conn.exec_driver_sql('PRAGMA table_info(family_shopping_items)')}
        additions={'quantity':"VARCHAR(32) NOT NULL DEFAULT ''",'unit':"VARCHAR(8) NOT NULL DEFAULT ''",
            'brand':"VARCHAR(120) NOT NULL DEFAULT ''",'category':"VARCHAR(24) NOT NULL DEFAULT 'other'",
            'expected_price':'INTEGER','store':"VARCHAR(120) NOT NULL DEFAULT ''",
            'priority':'BOOLEAN NOT NULL DEFAULT 0','stocked':'BOOLEAN NOT NULL DEFAULT 0',
            'source':"JSON NOT NULL DEFAULT '{}'"}
        for name,definition in additions.items():
            if name not in shopping_columns:conn.exec_driver_sql('ALTER TABLE family_shopping_items ADD COLUMN '+name+' '+definition)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_stock_owner_time ON product_mutations(owner_id,created_at)')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_assistant_owner_period ON assistant_exchanges(owner_id,period_start,status)')
        conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_assistant_owner_history ON assistant_exchanges(owner_id,mode,hidden,created_at,id)')
    from services.activity_audit import install
    install()


def create_session() -> Session:
    global __factory
    return __factory()