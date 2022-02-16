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

- Clone the [Auxin repo](https://github.com/mobilecoinofficial/auxin) and cd into it
    ``` bash
    git clone https://github.com/mobilecoinofficial/auxin.git
    cd auxin
    ```  

- Run `cargo build --release`
    ``` bash
    cargo build --release
    ```
    - Auxin might prompt you to install certain other dependencies such as `libssl`, or `pkg-config`, if it does so, install the appropriate packages and run `cargo build --release` again.  

- When the build finishes succesfully, `auxin-cli` will be in `./target/release/auxin-cli`. Verify that the installation succeded by invoking it.

    ```bash
    ./target/release/auxin-cli 
    ```
    ```bash
    auxin-cli 0.1.11
    ```
- Finally SymLink auxin-cli to your forest directory.
    ```bash
    cd ../forest
   
    ln -s ../auxin/target/release/auxin-cli .
   
    ./auxin-cli --version
    ```
    ```
    auxin-cli 0.1.11
    ```

### Send a message with Auxin ###

Since Auxin is fully compatible with signal-cli datastores, you should easily be able to send a message with it as you could with signal-cli. 
```bash
./auxin-cli --config . --user $BOT_NUMBER send $ADMIN -m "hello"
```
```bash
Successfully sent Signal message with timestamp: 1643064362099
```
You should have received a Signal message on your device. You will note that the syntaxt for sending a message with Auxin is very similar to Signal-cli. However there are some difference. For more information about Auxin-cli, checkout it's github repo, or invoke it's help dialog with `./auxin-cli --help`

### Run Hello Bot With Auxin ###

Auxin can run hellobot as good as signal-cli. To test hellobot with auxin, edit your dev_secrets file to the following:

```bash
NO_MEMFS=1
ROOT=.
SIGNAL=auxin
ADMIN=+15551111111
BOT_NUMBER=+15551234567
NO_DOWNLOAD=1
NO_MONITOR_WALLET=1
```
Then run hellobot as usual with:
```bash
pipenv run python -m sample_bots.hellobot
```
<br>


## Mobile Coin Wallet and Full Service ##

Now for the payments enabled part of payments-enabled Signal bot. Signal Pay uses a lightweight version of the Mobilecoin wallet called Fog. For running a bot however, we want to use the full service version of the wallet, appropriately named Full Service. The easiest way to create and use a Mobilecoin Wallet is with the Desktop Wallet which can be installed here.

Running the Mobilecoin Desktop Wallet creates an instance of Full Service. Full Service is, at its core, a client that talks to Mobilecoin consensus nodes and allows you to submit transactions and receive transactions. The Desktop Wallet uses Full Service to interact with the Mobilecoin Blockchain. You can use this instance of Full Service to create additional accounts.

Forest bots interact with a Full Service instance through HTTP. When you start the desktop wallet, it opens a socket on `http://127.0.0.1:9090/wallet`. You can put this URL in your dev_secrets file and the bot will be able to communicate with your wallet, meaning it can send and receive MOB and create separate accounts. If you don't want to use your main account for your bot, and in fact we recommend you don't, you can create a separate account in the desktop wallet and use that. That's what we'll be doing in this tutorial. If you want to host your bot on a server or cloud instance, you must enact additional security to ensure only authorised requests are being made to full-service. We'll explain one way to do that at the end.

For the purposes of the tutorial, do the following. Open the Desktop Wallet and create a new account called `paymebot`.

<img width=500px src="images/newaccount.png">

Once you've done that, you can put your Full Service URL and Full Service account name in your dev_secrets file:

``` bash
NO_MEMFS=1
ROOT=.
SIGNAL=auxin
ADMIN=+15551111111
BOT_NUMBER=+15551234567
NO_DOWNLOAD=1
FULL_SERVICE_URL=http://127.0.0.1:9090/wallet
FS_ACCOUNT_NAME=paymebot

```

With these, you're ready to run Echopay

## Echopay aka PayMeBot ##



Your account is a hash on that's tracked on Mobilecoin's blockchain. One's account is represented by a mnemonic phrase that's created along with the account. Full Service also allows you to manage imported account, you can import a wallet just by knowing it's entropy (the 12 word recovery phrase given at creation). Therefore be very careful with your entropy. Your local instance of full service stores information on a local database, with your entropy. Be very guarded with your Full Service instance. This is why you need additional security measures when running the wallet on a shared device or a server. Anyone who can make HTTP requests to full service has complete control to the accounts stored.

You never lose your wallet if you lose your full service instance, but you would lose your transaction history. Full service only keeps transaction history from the point upon which a wallet is created or imported.

it's ok to run a bot like this if you're just running locally on your computer. If you're trying to deploy to a server or a cloud. In any sort of production environment. 

- set up full-service somewhere, like locally. or with https://github.com/i-infra/cert-pinning-demo. if you use the later, you need to take the crypto_secrets file and append it to your dev_secrets.
- put your FULL_SERVICE_URL in dev_secrets
- set PROFILE=1
- forestbot will automatically generate a full-service account if there isn't one and set mobilecoin address field in the signal profile for you

you can either import an account manually, like in ipython with forest.payments_monitor.Mobster(FULL_SERVICE_URL).import_account(MNEMONIC), or put MNEMONIC in your dev_secrets (discouraged)



```bash
cp -r ../forest ../mc_util ../Pipfile* .
fly deploy --strategy immediate
```
