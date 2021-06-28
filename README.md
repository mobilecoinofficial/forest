`docker run --entrypoint /app/main -p 127.0.0.1:8080:8080 -P --env-file ./secrets -it <result from docker build .>`

`cat secrets | flyctl secrets import`

> flyctl deploy [<workingdirectory>] [flags]
>  --strategy string      The strategy for replacing running instances. Options are canary, rolling, bluegreen, or immediate. Default is canary
