Requires python3.9

## Bootstrapping Forestbot 

Use pipenv install to install deps. Install notes for Ubuntu Hirsuite in INSTALL.md

you'll need to grab [https://github.com/forestcontact/signal-cli], check out the stdio-generalized `./gradlew installDist`, and add a symlink from signal-cli/build/install/signal-cli/bin/signal-cli to the working directory. 'default-jre' should work for signal-cli. 

you also need to register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point. you can also grab one from the DB if you have access to secrets.

You can use `python3.9 -m forest.datastore upload --number` or `python3.9 -m forest.datastore sync --number` to mess with the DB. your secrets file should be named {prod,staging,dev}_secrets. you can use `ENV=prod python3.9 -m forest.datastore ...` to select said file accordingly.


## Running Forestbot Locally

To run locally for a testing enviornment, you can run 'ENV=dev' and 'docker build .' Alternatively to docker, you can use contactbot.py 

If you have secrets, 'python3.9 -m forest.datastore list_accounts' should show your available accounts. 

## Running Forestbot on fly.io

We use fly.io for hosting. You'll need flyctl.

To update secrets in fly:
`cat secrets | flyctl secrets import`

Deploys generally should be `--strategy immediate` to not risk the old instance receiving messages and advancing the ratchet after the new instance has already downloaded the state.

> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary
  
If things seem wrong, you can use `fly suspend`, the above to sync, use signal-cli locally to receive/send --endsession/trust identities/whatever, then `fly resume`


Code style: mypy and pylint should not have errors when you push. run black. prefer verbose, easier to read names over conciser ones.

TODO: elaborate on

- things we hold evident
- design considerations
- experiments tried
