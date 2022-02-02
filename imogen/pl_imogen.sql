create or replace function map_len (dict jsonb) returns integer as $$
    declare key_count integer; begin                                                                       
    select count (*) from jsonb_object_keys(dict) into key_count; return key_count; 
    end;
$$ language plpgsql;


CREATE OR REPLACE FUNCTION get_balance(queried_account text) RETURNS NUMERIC AS $$
    SELECT COALESCE ( SUM (amount_usd_cents) / 100, 0.0)  
    FROM imogen_ledger WHERE account=queried_account;
$$ LANGUAGE SQL;

CREATE TYPE enqueue_result AS (
    success boolean,
    paid boolean,
    balance_remaining boolean,
    queue_length integer,
    workers integer
);

CREATE OR REPLACE FUNCTION enqueue_prompt(prompt TEXT, _author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS enqueue_result AS $$ 
    DECLARE
        result enqueue_result;
        prompt_id INTEGER;
    BEGIN
        IF get_balance(_author) >= 0.10 THEN
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
                VALUES (prompt, true, _author, signal_ts, group_id, params, url)  RETURNING id INTO prompt_id;
            INSERT INTO imogen_ledger (account, amount_usd_cents, memo, ts) 
                VALUES(_author, -10, prompt_id::text, CURRENT_TIMESTAMP);
            SELECT true, true, get_balance(_author) >= 0.10 INTO result.success, result.paid, result.balance_remaining;
            SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned'AND paid=true INTO result.workers;
            SELECT count(*) FROM prompt_queue 
                WHERE (status='pending' OR status='assigned') AND paid=true 
                INTO result.queue_length;
        ELSEIF (SELECT count (id) <= 5 FROM prompt_queue WHERE author=_author AND (status='pending' OR status='assigned') AND paid=false) THEN
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
                VALUES (prompt, false, _author, signal_ts, group_id, params, url);
            SELECT true, false, false INTO result.success, result.paid, result.balance_remaining;
            SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned' AND paid=false INTO result.workers;
            SELECT count(*) FROM prompt_queue 
                WHERE (status='pending' OR status='assigned') AND paid=False
                INTO result.queue_length;
        ELSE
            SELECT false, false, false INTO result.success, result.paid, result.balance_remaining;
        END IF;
        RETURN result;
    END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enqueue_paid_prompt(prompt TEXT, author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS enqueue_result AS $$ 
    DECLARE
        result enqueue_result;
        prompt_id INTEGER;
    BEGIN
        IF get_balance(author) < 0.10 THEN
            SELECT false, true, false INTO result.success, result.paid, result.balance_remaining;
        ELSE 
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
                VALUES (prompt, true, author, signal_ts, group_id, params, url)  RETURNING id INTO prompt_id;
            INSERT INTO imogen_ledger (account, amount_usd_cents, memo, ts) 
                VALUES(author, -10, prompt_id::text, CURRENT_TIMESTAMP);
            SELECT true, true, get_balance(author) >= 0.10 INTO result.success, result.paid, result.balance_remaining;
        END IF;
        SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned' INTO result.workers;
        SELECT count(*) FROM prompt_queue 
            WHERE (status='pending' OR status='assigned') AND paid=true 
            INTO result.queue_length;
        RETURN result;
    END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enqueue_free_prompt(prompt TEXT, _author TEXT, signal_ts BIGINT, group_id TEXT, params TEXT, url TEXT)
RETURNS enqueue_result AS $$ 
    DECLARE
        result enqueue_result;
    BEGIN
        IF (SELECT count (id) > 5 FROM prompt_queue WHERE author=_author AND status='pending' AND paid=false) THEN
            SELECT false, false INTO result.success, result.paid;
        ELSE 
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url)
                VALUES (prompt, false, _author, signal_ts, group_id, params, url);
            SELECT true, false INTO result.success, result.paid;
        END IF;
        SELECT coalesce(count(distinct hostname), 0) FROM prompt_queue WHERE status='assigned' INTO result.workers;
        SELECT count(*) FROM prompt_queue 
            WHERE (status='pending' OR status='assigned') AND paid=False
            INTO result.queue_length;
        RETURN result;
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
