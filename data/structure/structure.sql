-- Структура requests.data без значений (для генерации синтетических данных)
WITH RECURSIVE walk(path, val) AS (
  SELECT ''::text, data FROM requests
  UNION ALL
  SELECT w.path || c.suffix, c.v
  FROM walk w
  CROSS JOIN LATERAL (
    -- ключи объекта; «странные» ключи (числа, email, uuid и т.п.) маскируются
    SELECT '.' || CASE WHEN k ~ '^[A-Za-z_][A-Za-z0-9_]*$' THEN k ELSE '<key>' END AS suffix, v
    FROM jsonb_each(CASE WHEN jsonb_typeof(w.val) = 'object' THEN w.val ELSE '{}'::jsonb END) AS e(k, v)
    UNION ALL
    -- элементы массива
    SELECT '[]', v
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(w.val) = 'array' THEN w.val ELSE '[]'::jsonb END) AS v
  ) c
)
SELECT path,
       jsonb_typeof(val)                                                      AS type,
       count(*)                                                               AS n,
       count(DISTINCT val) FILTER (WHERE jsonb_typeof(val) IN ('string','number','boolean')) AS distinct_vals,
       max(length(val #>> '{}')) FILTER (WHERE jsonb_typeof(val) = 'string')  AS max_len
FROM walk
WHERE path <> ''
GROUP BY 1, 2
ORDER BY 1, 2;

-- Колонки всех таблиц (только имена и типы)
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
