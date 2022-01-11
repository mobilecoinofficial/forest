If you've already followed the readme on the root of the directory you're ready to deploy echopay. Otherwise, please go do it now!

## High Level Overview ##

In this tutorial you will
* learn how to use auxin-cli
* learn how to set a payments address on auxin-cli
* send and receive payments with Signal
* deploy echopay, a bot that receives payments and then sends the money back

So, let's get started

## Auxin CLI ##

Auxin CLI is a rust-based Signal command line client that allows sending and receiving Signal messages. It is not yet a full replacement to Signal-cli, but it also supports certain features of Signal that Signal-cli does not. Namely, payments.

Here's how to install Signal-cli and activate Payments for your bot's signal account.

## Full Service ##

Run Full service locally



## Mobile Coin Wallet ##

- set up full-service somewhere, like locally. or with https://github.com/i-infra/cert-pinning-demo. if you use the later, you need to take the crypto_secrets file and append it to your dev_secrets.
- put your FULL_SERVICE_URL in dev_secrets
- set PROFILE=1
- forestbot will automatically generate a full-service account if there isn't one and set mobilecoin address field in the signal profile for you

you can either import an account manually, like in ipython with forest.payments_monitor.Mobster(FULL_SERVICE_URL).import_account(MNEMONIC), or put MNEMONIC in your dev_secrets (discouraged)



```bash
cp -r ../forest ../mc_util ../Pipfile* .
fly deploy --strategy immediate
```
