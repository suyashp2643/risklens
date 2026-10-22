from alembic import context
from risklens.storage import Base, database_engine

if context.is_offline_mode():
    raise RuntimeError('Use online PostgreSQL migrations; offline SQL mode is not supported')
def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()

connection = context.config.attributes.get('connection')
if connection is not None:
    migrate(connection)
else:
    engine = database_engine()
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
