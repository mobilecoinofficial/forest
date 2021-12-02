Requires python3.9

## Bootstrapping Forestbot

Use pipenv install to install deps. Install notes for Ubuntu Hirsuite in INSTALL.md

you'll need to grab [https://github.com/forestcontact/signal-cli], check out the stdio-generalized `./gradlew installDist`, and add a symlink from signal-cli/build/install/signal-cli/bin/signal-cli to the working directory. `default-jre` should work for signal-cli.

you also need to register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point. you can also grab one from the DB if you have access to secrets.

you can use `python3.9 -m forest.datastore upload --number` or `python3.9 -m forest.datastore sync --number` to mess with the DB. your secrets file should be named {prod,staging,dev}_secrets.

you can use `ENV=prod python3.9 -m forest.datastore` to select said file accordingly. <- deprecated? "ENV=x" alone in the pipenv seems to do the right thing.

## Running Forestbot Locally

You'll need your signal-cli symlinked to the forest-draft directory. `ln -s ~/signal-cli/build/install/signal-cli/bin/signal-cli .`

If you have secrets, `python3.9 -m forest.datastore list_accounts` should show your available accounts. Then you can start it with an available number: `python3.9 contactbot.py +5555555555`

If you have credentials locally and do not wish to have the datatore updated, the `core` forestbot can be launched with this command.

> sh -c 'DEBUG=true LOGFILES= BOT_NUMBER=+12406171474 NO_MEMFS=true NO_DOWNLOAD=true $(which python3) -m forest.core'



## Running in Docker Locally

`docker build -t contactbot .` then `docker run --env-file dev_secrets contactbot` should work?

## Running Forestbot on fly.io

We use fly.io for hosting. You'll need flyctl: `curl -L https://fly.io/install.sh | sh`. Ask for an invite to our fly organization, or add a payment method to your personal fly account. Use `fly auth` to login.

Create a fly app with `fly launch`. Use a unique-ish name. This is supposed to create a fly.toml. Don't deploy just yet, we still need to add secrets.

Before deploying for the first time, and afterwords to update secrets, run `cat dev_secrets | flyctl secrets import`. If you're managing multiple environments like prod and staging, make multiple secrets files with their own `BOT_NUMBER`, `DATABASE_URL`, etc. Name those files `staging_secrets`, `prod_secrets`, etc. Afterwords, if you want to run stuff locally using a different set of secrets, use e.g. `ENV=prod python3.9 contactbot.py`

Finally, run `fly deploy`. This will build the docker image, upload it to the fly registry, and actually deploy it to fly. After the first time, deploys generally should be `--strategy immediate` to not risk the old instance receiving messages and advancing the ratchet after the new instance has already downloaded the state.

> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary

`fly logs` will give you forestbot's output.

If things seem wrong, you can use `fly suspend`, the above to sync, use signal-cli locally to receive/send --endsession/trust identities/whatever, then `fly resume`


# Options and secrets

- `ENV`: if running locally, which {ENV}_secrets file to use. this is also optionally used as profile family name
- `BOT_NUMBER`: signal account being used
- `ADMIN`: primarily fallback recipient for invalid webhooks; may also be used to send error messages
- `DATABASE_URL`: Postgres DB
- `TELI_KEY`: token to authenticate with teli
- `URL_OVERRIDE`: url teli should post sms to. needed if not running on fly

## Binary flags
- `NO_DOWNLOAD`: don't download a signal-cli datastore, instead use what's in the current working directory
- `NO_MEMFS`: if this isn't set, MEMFS is started, making a fake filesystem in `./data` and used to upload the signal-cli datastore to the database whenever it is changed. if not `NO_DOWNLOAD`, also create an equivalent tmpdir at /tmp/local-signal, chdir to it, and symlink signal-cli process and avatar
- `NO_MONITOR_WALLET`: monitor transactions from full-service. relevent only if you're giving users a payment address to send mobilecoin not with signal pay.  has bugs
- `SIGNAL_CLI_PATH`: executable to use. useful for running graalvm tracing agent
- `MIGRATE`: run DB migrations (needed when creating a new DB) and set teli sms webhooks
- `LOGFILES`: create a debug.log
- `LOGLEVEL`: what log level to use for console logs (DEBUG, INFO, WARNING, ERROR). 
- `ORDER`: allow users to buy phonenumbers with `/order` and `/pay shibboleth`
- `GROUPS`: use group routes, allowing `/mkgroup` (aka `/query`), using groups to manage to/from context
- `ADMIN_METRICS`: send python and roundtrip timedeltas for each command to ADMIN
- 
## Other stuff

Code style: `mypy *py` and `pylint *py` should not have errors when you push. run `black`. prefer verbose, easier to read names over conciser ones.

TODO: elaborate on

- things we hold evident
- design considerations
- experiments tried

