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
    from services.activity_audit import install
    install()


def create_session() -> Session:
    global __factory
    return __factory()