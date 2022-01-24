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

Here's how to install Auxin-cli and activate Payments for your bot's signal account.

### Install prerequisites ###

You'll need a working rust enviornment on your system to build Auxin. 
- [Follow these instructions to install rust on your computer.](https://www.rust-lang.org/learn/get-started) 
- Then change your default rust toolchain to the [nightly channel](https://rust-lang.github.io/rustup/concepts/channels.html):
    ```bash
    rustup default nightly
    ```
- Finally verify that rust installed correctly by running `cargo --version`:
    ```bash
    cargo --version
    ```
    ```bash
    cargo 1.60.0-nightly (95bb3c92b 2022-01-18)
    ```  
<br>

### Building Auxin-cli from source

Once you have rust set up properly you can build Auxin-cli from source. 

- Clone the [Auxin repo](https://github.com/mobilecoinofficial/auxin)
    ``` bash
    ```

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
