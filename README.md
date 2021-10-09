Requires python3.9

Use pipenv install to install deps

you'll need to grab [https://github.com/forestcontact/signal-cli], check out the stdio-generalized `./gradlew installDist`, and add a symlink from signal-cli/build/install/signal-cli/bin/signal-cli to the working directory.

you also need to register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point. you can also grab one from the DB if you have access to secrets.

You can use `./datastore.py upload --number` or `./datastore.py sync --number` to mess with the DB. your secrets file should be named {prod,staging,dev}_secrets. you can use `ENV=prod ./datastore ...` to select said file accordingly.

If things seem wrong, you can use `fly suspend`, the above to sync, use signal-cli locally to receive/send --endsession/trust identities/whatever, then `fly resume`


We use fly.io for hosting. You'll need flyctl.

To update secrets in fly:
`cat secrets | flyctl secrets import`

Deploys generally should be `--strategy immediate` to not risk the old instance receiving messages and advancing the ratchet after the new instance has already downloaded the state.

> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary



TODO: elaborate on

- things we hold evident
- design considerations
- experiments tried
