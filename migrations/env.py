import asyncio

from alembic import context

from db import make_engine


def migrate(connection):
    context.configure(connection=connection, version_table="rag_alembic_version")
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = make_engine()
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


asyncio.run(main())
