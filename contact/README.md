Requires python3.9

## Bootstrapping Forestbot

Use pipenv install to install deps. Install notes for Ubuntu Hirsuite in INSTALL.md

you'll need to grab [https://github.com/forestcontact/signal-cli], check out the stdio-generalized `./gradlew installDist`, and add a symlink from signal-cli/build/install/signal-cli/bin/signal-cli to the working directory. `default-jre` should work for signal-cli.

you also need to register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point. you can also grab one from the DB if you have access to secrets.

you can use `forest/datastore.py upload --number` or `forest/datastore.py sync --number` to mess with the DB. your secrets file should be named {prod,staging,dev}_secrets.

you can use `forest/datastore.py` to select said file accordingly. Use `ENV=prod forest..` to use prod_secrets, etc.

## Running Forestbot Locally

You'll need your signal-cli symlinked to the forest-draft directory. `ln -s ~/signal-cli/build/install/signal-cli/bin/signal-cli .`

If you have secrets, `forest/datastore.py list_accounts` should show your available accounts. Then you can start it with an available number: `python3.9 contactbot.py +5555555555`

If you have credentials locally and do not wish to have the datatore updated, the `core` forestbot can be launched with this command.

> sh -c 'BOT_NUMBER=+12406171474 $(which python3) -m forest.core'


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


- `ENV`: if running locally, which {ENV}_secrets file to use. 
- `BOT_NUMBER`: the number for the bot's signal account
- `ADMIN`: admin's phone number, primarily as a fallback recipient for invalid webhooks; may also be used to send error messages and metrics.
- `DATABASE_URL`: URL for the Postgres database to store the signal keys in as well as other information.
- `FULL_SERVICE_URL`: URL for [full-service](https://github.com/mobilecoinofficial/full-service) instance to use for sending and receiving payments
- `CLIENTCRT`: client certificate to connect to ssl-enabled full-service.
- `ROOTCRT`: certificate to validate full-service.
- `MNEMONIC`: account to import for full-service. Not Secure.
- `SIGNAL`: which signal client to use. can be 'signal-cli' or 'auxin-cli'. Defaults to auxin.
- `ROOT_DIR`: specify the directory where the data file is stored, as well as where the signal-cli executable is. Defaults to `/tmp/local-signal` if DOWNLOAD, `/app` if running on fly, and `.` otherwise
- `SIGNAL_CLI_PATH`: specify where the signal-cli executable is if it is not in ROOT_DIR.
- `LOGLEVEL`: what log level to use for console logs (DEBUG, INFO, WARNING, ERROR). Defaults to DEBUG
- `TYPO_THRESHOLD`: maximum normalized Levenshtein edit distance for typo correction. 0 is only exact matches, 1 is any match. Default: 0.3
- `SIGNAL_CLI_PATH`: executable to use. useful for running graalvm tracing agent
- `TELI_KEY`: token to authenticate with teli
- `URL_OVERRIDE`: url teli should post sms to. needed if not running on fly


## Binary flags

- `DOWNLOAD`: download/upload datastore from the database instead of using what's in the current working directory.
- `AUTOSAVE`: start MEMFS, making a fake filesystem in `./data` and used to upload the signal-cli datastore to the database whenever it is changed. If `DOWNLOAD`, also create an equivalent tmpdir at /tmp/local-signal, chdir to it, and symlink signal-cli process and avatar.
- `MONITOR_WALLET`: monitor transactions from full-service. Relevant only if you're giving users a payment address to send mobilecoin to instead of using signal pay.  Experimental, do not use.
- `LOGFILES`: create a debug.log.
- `ADMIN_METRICS`: send python and roundtrip timedeltas for each command to ADMIN.
- `ENABLE_MAGIC`: use string distence and expansions 
- `MIGRATE`: run DB migrations (needed when creating a new DB) and set teli sms webhooks
- `ORDER`: allow users to buy phonenumbers with `/order` and `/pay shibboleth`
- `GROUPS`: use group routes, allowing `/mkgroup` (aka `/query`), using groups to manage to/from context
- `ADMIN_METRICS`: send python and roundtrip timedeltas for each command to ADMIN

## Contributing

We accept Issues and Pull Requests. These are our style guides:

Code style: Ensure that `mypy *py` and `pylint *py` do not return errors before you push.

Use [black](https://github.com/psf/black) to format your python code. Prefer verbose, easier to read names over conciser ones.

Install black pre-commit hook with `ln -s (readlink -f .githooks/pre-commit) .git/hooks/pre-commit` on fish, or `ln -s $(readlink -f .githooks/pre-commit) .git/hooks/pre-commit` on bash. Requires black to be installed.
