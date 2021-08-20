TODO:
- use jsonRpc (requires graal fix)
- fix payments graal
- actually split up your-forest-bot
- find receipt; bundle full-service 


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
