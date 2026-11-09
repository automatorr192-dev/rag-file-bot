-- Owner 0 is reserved for the shared demo corpus.
WITH semantic AS (
    SELECT c.id, row_number() OVER (
        ORDER BY c.embedding <=> CAST(CAST(:embedding AS text) AS vector), c.id
    ) AS rank
    FROM rag.chunks c JOIN rag.documents d ON d.id = c.document_id
    WHERE d.owner = :owner AND :strategy IN ('semantic', 'hybrid')
    ORDER BY rank LIMIT 20
), lexical AS (
    SELECT c.id, row_number() OVER (
        ORDER BY ts_rank_cd(c.keywords, websearch_to_tsquery('russian', :question)) DESC, c.id
    ) AS rank
    FROM rag.chunks c JOIN rag.documents d ON d.id = c.document_id
    WHERE d.owner = :owner AND :strategy IN ('keyword', 'hybrid')
      AND c.keywords @@ websearch_to_tsquery('russian', :question)
    ORDER BY rank LIMIT 20
)
SELECT c.id, d.name, c.location, c.body
FROM semantic s FULL JOIN lexical l USING (id)
JOIN rag.chunks c ON c.id = COALESCE(s.id, l.id)
JOIN rag.documents d ON d.id = c.document_id
ORDER BY COALESCE(1.0 / (50 + s.rank), 0) + COALESCE(1.0 / (50 + l.rank), 0) DESC, c.id
LIMIT 5
