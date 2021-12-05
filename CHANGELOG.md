TODO:
- migrate the forest module into your-signal-bot or something like it

## 1.0.0

- `@requires_admin`, `@hide`, `@takes_number` decorators (#43)
- full-service ssl (#38)
- tiamat integration testing, including payments (#39, #29) 
- workflow checks!
- use mainline signal-cli's jsonRpc
- use auxin!!! with sending payments! (#36)
- PayBot to handle payments
- prometheus metrics at <forest-prom.fly.dev>, grafana at <auge.fly.dev>  (#31)
- recover from exceptions and send them to admin
- /ping /pong and pong_handler could be also be used for integration tests (#27, #22)
- imogen: wikiart model, style prefixes


## 0.5.3

- make_rule command for contactbot (needs to be admin-only)
- more example bots: evilbot (starts and stops typing when you do), "almost as secure as ssh"
- typing field on messages
- allow overwriting default response
- retry receipt decoding if the transaction is pending
- fix a bug where commands wouldn't be sent after signal-cli was restarted
- fix payment bugs
- allow different full-service urls
- get_balance
- get_secret treats "0", "false", "no" as falsey; doesn't reload the same secrets file
- fix logging to files
- imogen: urldecode destinations and messages, b58encode groupIds, dump_queue (needs to actually send what was dumped)

## 0.5.2 

- autosave.py is a separate file. datastore can be invoked directly again. tmpdir setup moved to a different function from start_memfs
- example dockerfile that only downloads a datastore and does nothing else that could theoretically be included in other stuff

## 0.5.1 

- payments code is moved into Bot and a new Mobster class that also handles the full-service http sesh, mob price, and financial tables 
- invoice table maps unique amounts to users. registration payment monitoring code uncommented and mostly fixed, but rereads (with spam) the transaction log and re-credits 
- /help: closes issue #14, though there are obvious improvements (see TODOs)
- restarts signal-cli when it exits to mitigate memory leaks


## 0.5

- restructure into a forest/ package
- Bot.__init__ schedules `create_process` and `handle_messages`
- payment reciepts
- balances

## 0.4

- split up our logic + teli from the bot framework
- basic payments
- script for deduplicating numbers across environments
- datastore as a cli program; memfs and downloading are optional
- trace loglevel 
- save last downloaded/uploaded timestamps to check if someone else has uploaded
- /user/{number} webhook endpoint

## 0.3

added:
- use signal-cli 8.4.1
- try using sourceName to greet user if available
- set users as admins in groups

fixed:
- setting profile works
- improve logging
- groups work
- only the last group for a conversation is used
- user-agent in fallbacks

## 0.2.1
- close connection pools
- santize reply routing
- don't print receiptMessages

## 0.2

- groups
- replies
- ordering
- printerfact
- database table names changed, use consistent number formats
