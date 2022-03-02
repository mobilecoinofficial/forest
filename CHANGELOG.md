## 1.2.1

- use `SIGNAL_PATH` instead of `SIGNAL_CLI_PATH`; it uses `which signal-cli` or `which auxin-cli` as appropriate as a fallback. (#145)

## 1.2.0

- Breaking change! Disambiguate `.get_address(..)` method. (#121)
- `Mobster.get_my_address()` returns the MOB address associated with the full service account
- `PayBot.get_pay_address(signal_user_id)` returns the MOB address associated associated with a Signal user

## 1.1.0

- `AUTOSAVE`, `DOWNLOAD` replace `NO_MEMFS`, `NO_DOWNLOAD`. Requires updating secrets for most bots.

## 1.0.10

- captchas! (#124, #126)
- QuestionBot can `ask_intable`, `ask_floatable`, and `require_first_device` for those pesky profile keys (#122)
- small OSX portability fix (#137)

## 1.0.9

- restart handle_messages after crashing and fix backoff logic (#99)
- refactor forest internals, improving naming schemes for signal clients. Notably replaces old things with inbox/outbox, `signal_rpc_request`, and `wait_for_response` (#105)
- many small fixes: MOBot Helper (#107, #111), echopay (#114), pong semantics (#110)

## 1.0.8

- QuestionBot uuids, better error handling for failed to `build_transaction`, `is_admin` supports uuid, eval can access globals() (#104)
- Imogen manpage! (#79)


## 1.0.7

- add QuestionBot (#93)
- Prevent tx receipt delivery on tx failure (#97)
- MOBot Helper: Conversational UI, q&a workflow for creation, confirmations for redemption, no slashes, mild rebranding, etc (#98)
- aPersistDict improvements (#101)

## 1.0.6

- Use ULIDs for JSON RPC ids (#92)
- Switch to Levenshtein distance, improve matching logic, and accept mentions as commands (#94)
- Add pdict and pdictng offering PersistDict and aPersistDict, easy state management abstractions (#95)

## 1.0.5

- Track sending rate limit and pause before hitting it (#82)
- When sending a message fails with 413 (rate limit), retry sending that message
- When sending a payment, monitor full-service for the payment to be complete before sending a message about it
- Add admin debugging commands: /eval for running snippets of python and /fsr for full-service requests  (#88)

## 1.0.4

- Jaccard distance for correcting typo. Use first word as command.

## 1.0.3

- do_update sets profile picture from attachment (#56)
- redirect invalid requests to tiprat, which times out slowly (#57)
- hide admin commands better (#67)
- github actions work now (#68)
- add documentation!! includes changing default full-service (#62)

## 1.0.2

- Move contactbot to it's own subdirectory.
- bugfixes from [PR 54](https://github.com/mobilecoinofficial/forest/pull/54/files)
- add mobfriend bot source, deployed to +1(223)222-2922
- add Code of Conduct for the project

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
