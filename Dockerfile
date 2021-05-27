FROM ghcr.io/graalvm/graalvm-ce:latest as sigbuilder
ENV GRAALVM_HOME=/opt/graalvm-ce-java11-21.1.0/ 
SHELL ["/usr/bin/bash", "-c"]
WORKDIR /app
RUN microdnf install -y git zlib-devel && rm -rf /var/cache/yum
RUN gu install native-image
RUN git clone https://github.com/i-infra/signal-cli
WORKDIR /app/signal-cli
RUN git fetch -a && git checkout 8132f72d85ae8693e0ac8388268223e42b73023b 
RUN ./gradlew build && ./gradlew installDist
RUN md5sum ./build/libs/* 
RUN ./gradlew assembleNativeImage

FROM ubuntu:focal as libbuilder
WORKDIR /app
RUN apt update
RUN apt install -yy python3.8 python3-pip python3-venv libfuse2
RUN python3.8 -m venv /app/venv && pip3 install pipenv
COPY Pipfile.lock Pipfile /app/
RUN VIRTUAL_ENV=/app/venv pipenv install 
RUN VIRTUAL_ENV=/app/venv pipenv run pip uninstall dataclasses -y

FROM ubuntu:focal
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update
RUN apt install -y python3 wget libfuse2 kmod
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

# v5.12.2 for fly.io
RUN wget -q -O fuse.ko "https://public.getpost.workers.dev/?key=01F54FQVAX85R1Y98ACCXT2AGT&raw"
RUN wget -q -O websocat https://github.com/vi/websocat/releases/download/v1.8.0/websocat_amd64-linux-static
RUN wget -q -O cloudflared https://github.com/cloudflare/cloudflared/releases/download/2021.4.0/cloudflared-linux-amd64
#RUN wget -q -O jq https://github.com/stedolan/jq/releases/download/jq-1.6/jq-linux64
#RUN wget -q -O curl https://github.com/moparisthebest/static-curl/releases/download/v7.76.1/curl-amd64
#RUN chmod +x ./curl ./jq ./cloudflared ./websocat
RUN chmod +x ./cloudflared ./websocat
COPY --from=sigbuilder /app/signal-cli/build/native-image/signal-cli /app
# for signal-cli's unpacking of native deps
COPY --from=sigbuilder /lib64/libz.so.1 /lib64
COPY --from=libbuilder /app/venv/lib/python3.8/site-packages /app/
COPY ./forest_tables.py ./fuse.py  ./mem.py  ./pghelp.py ./main.py /app/
ENTRYPOINT ["/usr/bin/python3", "/app/main.py"]
