`docker run --entrypoint /app/main -p 127.0.0.1:8080:8080 -P --env-file ./secrets -it <result from docker build .>`

`cat secrets | flyctl secrets import`


you'll need to grab [https://github.com/forestcontact/signal-cli, `./gradlew build`, and add a symlink to the working directory, and register an account -- you can use https://github.com/forestcontact/go_ham/blob/main/register.py or https://github.com/forestcontact/message-in-a-bottle as a starting point


> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary

