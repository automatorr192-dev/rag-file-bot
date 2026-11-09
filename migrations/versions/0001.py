from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA rag")
    op.execute("""
        CREATE TABLE rag.documents (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            owner bigint NOT NULL,
            name text NOT NULL,
            digest text NOT NULL,
            UNIQUE(owner, digest)
        )
    """)
    op.execute("""
        CREATE TABLE rag.chunks (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            document_id bigint NOT NULL REFERENCES rag.documents ON DELETE CASCADE,
            location text NOT NULL,
            body text NOT NULL,
            embedding vector(384) NOT NULL,
            keywords tsvector GENERATED ALWAYS AS (to_tsvector('russian', body)) STORED
        )
    """)
    op.execute("CREATE INDEX ON rag.chunks(document_id)")
    op.execute("CREATE INDEX ON rag.chunks USING gin(keywords)")
    op.execute("""
        CREATE TABLE rag.turns (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            owner bigint NOT NULL,
            mode text NOT NULL CHECK (mode IN ('demo', 'files')),
            question text NOT NULL,
            answer text NOT NULL,
            sources jsonb NOT NULL
        )
    """)
    op.execute("CREATE INDEX ON rag.turns(owner, mode, id DESC)")
    op.execute("""
        CREATE TABLE rag.users (
            owner bigint PRIMARY KEY,
            mode text NOT NULL DEFAULT 'demo' CHECK (mode IN ('demo', 'files')),
            upload_allowed boolean NOT NULL DEFAULT false
        )
    """)
    op.execute("""
        CREATE TABLE rag.quota (
            owner bigint NOT NULL,
            day date NOT NULL,
            used integer NOT NULL,
            PRIMARY KEY(owner, day)
        )
    """)


def downgrade():
    for table in ("quota", "users", "turns", "chunks", "documents"):
        op.execute(f"DROP TABLE rag.{table}")
    op.execute("DROP SCHEMA rag")
