CREATE OR REPLACE FUNCTION get_balance(account text) RETURNS NUMERIC AS $$
    SELECT COALESCE ( SUM (amount_usd_cents) / 100, 0.0)  
    FROM imogen_ledger WHERE account=account;
$$ LANGUAGE SQL;

CREATE TYPE enqueue_result (
    success boolean,
    queue_length integer,
    workers integer
);


CREATE OR REPLACE FUNCTION enqueue_paid_prompt(prompt TEXT, author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS setof record AS $$ 
    DECLARE
        status enqueue_result;
        id INTEGER;
    BEGIN
        IF get_balance(author) > 0.10 THEN
            SELECT false INTO status.success;
        ELSE 
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
                VALUES (prompt, true, author, signal_ts, group_id, params, url)  RETURNING id INTO id;
            INSERT INTO imogen_ledger (account, amount_usd_cents, memo, ts) 
                VALUES(author, 10, id::text, CURRENT_TIMESTAMP);
            SELECT true INTO status.success;
        END IF;
        SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned' INTO status.workers;
        SELECT count(id) FROM prompt_queue 
            WHERE status='pending' OR status='assigned' AND paid=true 
            INTO status.queue_length;
        RETURN output;
    END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enqueue_free_prompt(prompt TEXT, author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS setof record AS $$ 
    DECLARE
        status enqueue_result;
        id INTEGER;
    BEGIN
        IF (SELECT count (id) > 5 FROM prompt_queue WHERE author=author AND status='pending') THEN
            SELECT false INTO status.success;
        ELSE 
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
                VALUES (prompt, true, author, signal_ts, group_id, params, url)  RETURNING id INTO id;
            INSERT INTO imogen_ledger (account, amount_usd_cents, memo, ts) 
                VALUES(author, 10, id::text, CURRENT_TIMESTAMP);
            SELECT true INTO status.success;
        END IF;
        SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned' INTO status.workers;
        SELECT count(id) FROM prompt_queue 
            WHERE status='pending' OR status='assigned' 
            INTO status.queue_length;
        RETURN output;
    END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION get_prompt(paid BOOLEAN, claimant TEXT) RETURNS record AS $$
    DECLARE
        returned_id INTEGER;
        _prompt RECORD;
    BEGIN 
        UPDATE prompt_queue SET status='pending', assigned_at=null
        WHERE status='assigned' AND assigned_at  < (now() - interval '10 minutes');
        IF paid THEN
            SELECT prompt_queue.id FROM prompt_queue WHERE status='pending' AND paid IS TRUE ORDER BY signal_ts ASC LIMIT 1 INTO returned_id;
        ELSE
            SELECT prompt_queue.id FROM prompt_queue WHERE status='pending' ORDER BY signal_ts ASC LIMIT 1 INTO returned_id;
        END IF;
--        IF returned_id IS NULL THEN
--            RETURN (SELECT -1 AS prompt_id);
--        END IF;
        UPDATE prompt_queue SET status='assigned', assigned_at=CURRENT_TIMESTAMP, hostname=claimant WHERE prompt_queue.id = id 
        RETURNING id AS prompt_id, prompt, params, url INTO _prompt;
-- not great, returns this as a tuple without column names
        RETURN _prompt;
    END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_uploading (id INTEGER, loss FLOAT, elapsed_gpu INTEGER, filepath TEXT) RETURNS VOID AS $$
    UPDATE imogen_ledger SET amount_usd_cents = 0.10 / 120 * elapsed_gpu  WHERE memo=id::text;
    UPDATE prompt_queue SET status='uploading', loss=loss, elapsed_gpu=elapsed_gpu, filepath=filepath WHERE id=id;
$$ LANGUAGE sql;
