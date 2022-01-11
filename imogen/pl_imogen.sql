CREATE OR REPLACE FUNCTION get_balance(account text) RETURNS NUMERIC AS $$
    SELECT COALESCE ( SUM (amount_usd_cents) / 100, 0.0)  
    FROM imogen_ledger WHERE account=account;
$$ LANGUAGE SQL;


CREATE OR REPLACE FUNCTION enqueue_prompt(prompt TEXT, author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS setof record AS $$ 
    DECLARE 
        is_paid BOOLEAN;
        id INTEGER;
        output RECORD;
    BEGIN
        is_paid := get_balance(author) > 0.10 ;
        -- IF NOT is_paid AND (SELECT count (id) < 6 FROM prompt_queue WHERE author=author AND status='pending') THEN
        --     SELECT -1, -1, -1 INTO output;
        --     RETURN output;
        -- END IF;
        INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
            VALUES (prompt, is_paid, author, signal_ts, group_id, params, url)  RETURNING id INTO id;
        IF is_paid THEN
            INSERT INTO imogen_ledger (account, amount_usd_cents, memo, ts) 
            VALUES(author, 10, id::text, CURRENT_TIMESTAMP);
        END IF;
        SELECT
            (SELECT count(id) FROM prompt_queue WHERE status='pending' OR status='assigned') as queue_length,
            (SELECT count(id) FROM prompt_queue WHERE status='pending' OR status='assigned' AND paid=true) as paid_queue_length),
            (SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned') as workers,
            is_paid as paid
            INTO output;
        RETURN output;
    END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_prompt(paid BOOLEAN, hostname TEXT) RETURNS setof record AS $$
    DECLARE
        id INTEGER;
    BEGIN 
        UPDATE prompt_queue SET status='pending', assigned_at=null
        WHERE status='assigned' AND assigned_at  < (now() - interval '10 minutes');
        IF paid THEN
            SELECT id FROM prompt_queue WHERE status='pending' AND paid IS TRUE ORDER BY signal_ts ASC LIMIT 1 INTO id;
        ELSE
            SELECT id FROM prompt_queue WHERE status='pending' ORDER BY signal_ts ASC LIMIT 1 INTO id;
        END IF;
        IF id IS NULL THEN
            RETURN 0;
        END IF;
        UPDATE prompt_queue SET status='assigned', assigned_at=CURRENT_TIMESTAMP, hostname=hostname WHERE id = id 
        RETURNING id AS prompt_id, prompt, params, url;
    END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_uploading (id SERIAL, loss FLOAT, elapsed_gpu INTEGER, filepath TEXT) RETURNS VOID AS $$
    UPDATE imogen_ledger SET amount_usd_cents = 0.10 / 120 * elapsed_gpu  WHERE memo=id::text;
    UPDATE prompt_queue SET status='uploading', loss=loss, elapsed_gpu=elapsed_gpu, filepath=filepath WHERE id=id;
$$ LANGUAGE sql;
