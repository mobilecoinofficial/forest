Requires python3.9

Use pipenv install to install deps. Install notes for Ubuntu Hirsuite in INSTALL.md

you'll need to grab [https://github.com/forestcontact/signal-cli], check out the stdio-generalized `./gradlew installDist`, and add a symlink from signal-cli/build/install/signal-cli/bin/signal-cli to the working directory.

you also need to register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point. you can also grab one from the DB if you have access to secrets.

you'll need to grab [https://github.com/forestcontact/signal-cli], check out the stdio-generalized `./gradlew installDist`, and add a symlink from signal-cli/build/install/signal-cli/bin/signal-cli to the working directory.

you also need to register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point. you can also grab one from the DB if you have access to secrets.

You can use `python3.9 -m forest.datastore upload --number` or `python3.9 -m forest.datastore sync --number` to mess with the DB. your secrets file should be named {prod,staging,dev}_secrets. you can use `ENV=prod python3.9 -m forest.datastore ...` to select said file accordingly.

If things seem wrong, you can use `fly suspend`, the above to sync, use signal-cli locally to receive/send --endsession/trust identities/whatever, then `fly resume`


We use fly.io for hosting. You'll need flyctl.

To update secrets in fly:
`cat secrets | flyctl secrets import`

Deploys generally should be `--strategy immediate` to not risk the old instance receiving messages and advancing the ratchet after the new instance has already downloaded the state.

> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary


Code style: mypy and pylint should not have errors when you push. run black. prefer verbose, easier to read names over conciser ones.

TODO: elaborate on

- things we hold evident
- design considerations
- experiments tried

# Options and secrets

- BOT_NUMBER: signal account being used
- ADMIN: primarily fallback recipient for invalid webhooks
- DATABASE_URL: postgres db
- TELI_KEY: token to authenticate with teli

## Flags
- `ENV`: which {ENV}_secrets to use and optionally set as profile family name 
- `NO_DOWNLOAD`: don't download a datastore, use pwd 
- `NO_MEMFS`: don't autosave. if not `NO_DOWNLOAD`, also create an equivalent tmpdir at /tmp/local-signal and symlink signal-cli process and avatar
- `NO_MONITOR_WALLET`: don't monitor transactions from full-service
- `SIGNAL_CLI_PATH`: executable to use. useful for running graalvm tracing agent
- `MIGRATE`: run db migrations and set teli sms webhooks
- `LOGFILES`: create a debug.log 
- `ORDER`: allow users to buy phonenumbers
- `GROUPS`: use group routes
