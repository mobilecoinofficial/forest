FROM ubuntu:latest
RUN apt-get update && apt-get -yy install wget
RUN wget https://github.com/mobilecoinofficial/full-service/releases/download/v1.0.0/mainnet-full-service-release-linux-penryn-1.0.0.tar.gz
RUN tar xf mainnet-full-service-release-linux-penryn-1.0.0.tar.gz
WORKDIR ./mainnet-full-service-release-linux-penryn-1.0.0
COPY ./run.sh .
ENTRYPOINT ["/bin/bash", "./run.sh"]
