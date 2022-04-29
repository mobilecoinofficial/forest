## 1.2.9.1

- remove thank you message in handle_payment
- echopay readme
- check if there's enough MOB for `split_txos_slow`
- better attachment downloading

## 1.2.9

* better test coverage for question bots (#181)
* add `get_pmob_balance`, autocreate tables (#193)
* better ergonomics for setting up paybots (#197)

## 1.2.8

- bump pdict-ng docs (#183)
- add send_typing (#176)
- check if ROOT_DIR/SIGNAL exists before using it and remove copied lines (#186)
- to_dict base class so quotes get rendered right (#188)
- make /wallet optional in the full service url (#191)
- fix is_admin for groups (#196)

## 1.2.7

- new user_activity table holds first seen and last seen per-user per-bot. requires `METRICS_SALT` (#157)
- if there isn't a txo big enough to split into 15, split into however many we can (#182)

## 1.2.6

- notes on the new captcha and installing on arch (#166)
- quote messages to reply in TalkBack (#170)
- improved `ask_yesno_question` (#172)
- `ask_email_question` (#175)
- improve type hints (#171)
- add values() and items() to pdict (#179)

## 1.2.5

- persistent synonyms with pdictng (#134)
- `ask_address_question` (#164)
- test refactors and new question tests (#168)
- better dicts in logs, don't log pongs (#162)
- `set_confirm_timeout` defaults to 60, preventing a common footgun (#167)
- fix autosave for auxin-cli (#165)
- fix is_admin (#174)

## 1.2.4

- /restart endpoint restarts proc; also refactor asyncio task handlers (#155)
- `ask_multiple_choice_question` (#156)
- bump mobot helper to use poetry properly (#158)
- clean up insecure and evil bots (#159)
- send event owners blasts from other admins regardless if they're a list member or not (#163)
- SynonymBot and synonym decorator (#134)

## 1.2.3

- new flags: ADMINS, ADMIN_GROUP, UPLOAD (upload-only). (#149)
- `last_node_name column` in `signal_accounts` table, like `active_node_name` and also they include app name. `list_accounts` uses your timezone
- refactor command matching and  message text parsing. include `device_id`, reaction, and quote. shouldn't have breaking changes. 
- move commands from Bot to ExtrasBot (could be renamed), drop invoices, allow using separate ledgers, time postgres queries

## 1.2.2

- fasterpKVStoreClient (#112). probably need to change PAUTH if you're using pdict
- upload requires note (#147)
- switch from pipenv to poetry (#148)

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
