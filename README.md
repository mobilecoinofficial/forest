
You can use `./datastore.py upload --number` or `./datastore.py sync --number` to mess with the DB. your secrets file should be named {prod,staging,dev}_secrets. you can use `ENV=prod ./datastore ...` to select said file accordingly.

If things seem wrong, you can use `fly suspend`, the above to sync, use signal-cli locally to receive/send --endsession/trust identities/whatever, then `fly resume`



To update secrets:
`cat secrets | flyctl secrets import`


you'll need to grab [https://github.com/forestcontact/signal-cli, `./gradlew build`, and add a symlink to the working directory, and register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point


Deploys generally should be `--strategy immediate` to not risk the old instance receiving messages and advancing the ratchet after the new instance has already downloaded the state.

> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary




