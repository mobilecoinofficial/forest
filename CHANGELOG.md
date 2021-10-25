TODO:
- use jsonRpc (requires graal payments against mainline)
- migrate the forest module into your-signal-bot or something like it

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
