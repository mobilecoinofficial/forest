CREATE OR REPLACE FUNCTION get_balance(account text) RETURNS NUMERIC AS $$
    SELECT COALESCE ( SUM (amount_usd_cents) / 100, 0.0)  
    FROM imogen_ledger WHERE account=account;
$$ LANGUAGE SQL;


CREATE OR REPLACE FUNCTION enqueue_prompt(prompt TEXT, author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS integer AS $$ 
    DECLARE 
        output TEXT;
        is_paid BOOLEAN;
        id SERIAL;
    BEGIN
        is_paid := get_balance(author) > 0.10 ;
        IF NOT is_paid AND (SELECT count (id) < 6 FROM prompt_queue WHERE author=author AND status='pending') THEN
            RETURN -1;
        END IF;
        id := INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
        VALUES (prompt, is_paid, author, signal_ts, group_id, params, url)  RETURNING id;
        IF is_paid THEN
            INSERT INTO imogen_ledger (account, amount_usd_cents, memo, ts) 
            VALUES(author, 10, id::text, CURRENT_TIMESTAMP);
        END IF;
        RETURN SELECT count(id) AS len FROM prompt_queue WHERE status='pending' OR status='assigned';
    END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_prompt(paid BOOLEAN, hostname TEXT) RETURNS setof record AS $$
    DECLARE
        id SERIAL;
    BEGIN 
        UPDATE prompt_queue SET status='pending', assigned_at=null
        WHERE status='assigned' AND assigned_at  < (now() - interval '10 minutes');
        IF paid THEN
            id := SELECT id FROM prompt_queue WHERE status='pending' AND paid IS TRUE ORDER BY signal_ts ASC LIMIT 1;
        ELSE
            id := SELECT id FROM prompt_queue WHERE status='pending' ORDER BY signal_ts ASC LIMIT 1;
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

