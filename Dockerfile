FROM ghcr.io/graalvm/graalvm-ce:latest as sigbuilder
ENV GRAALVM_HOME=/opt/graalvm-ce-java11-21.1.0/ 
SHELL ["/usr/bin/bash", "-c"]
WORKDIR /app
RUN microdnf install -y git zlib-devel && rm -rf /var/cache/yum
RUN gu install native-image
RUN git clone https://github.com/forestcontact/signal-cli
WORKDIR /app/signal-cli
RUN git fetch -a && git checkout 4b082df    #stdio-generalized 
RUN ./gradlew build && ./gradlew installDist
RUN md5sum ./build/libs/* 
RUN ./gradlew assembleNativeImage

FROM ubuntu:hirsute as libbuilder
WORKDIR /app
RUN ln --symbolic --force --no-dereference /usr/share/zoneinfo/EST && echo "EST" > /etc/timezone
RUN apt update
RUN DEBIAN_FRONTEND="noninteractive" apt install -yy python3.9 python3.9-venv libfuse2 pipenv
RUN python3.9 -m venv /app/venv
COPY Pipfile.lock Pipfile /app/
RUN VIRTUAL_ENV=/app/venv pipenv install 
RUN VIRTUAL_ENV=/app/venv pipenv run pip uninstall dataclasses -y

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update
RUN apt install -y python3.9 wget libfuse2 kmod #npm
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

# v5.12.2 for fly.io
RUN wget -q -O fuse.ko "https://public.getpost.workers.dev/?key=01F54FQVAX85R1Y98ACCXT2AGT&raw"
#RUN sudo insmod fuse.ko
#RUN wget -q -O websocat https://github.com/vi/websocat/releases/download/v1.8.0/websocat_amd64-linux-static
#RUN wget -q -O cloudflared https://github.com/cloudflare/cloudflared/releases/download/2021.4.0/cloudflared-linux-amd64
#RUN wget -q -O jq https://github.com/stedolan/jq/releases/download/jq-1.6/jq-linux64
#RUN wget -q -O curl https://github.com/moparisthebest/static-curl/releases/download/v7.76.1/curl-amd64
#RUN chmod +x ./curl ./jq ./cloudflared ./websocat
#RUN chmod +x ./cloudflared ./websocat
COPY --from=sigbuilder /app/signal-cli/build/native-image/signal-cli /app
# for signal-cli's unpacking of native deps
COPY --from=sigbuilder /lib64/libz.so.1 /lib64
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./avatar.png ./datastore.py ./forest_tables.py ./fuse.py  ./mem.py  ./pghelp.py ./main.py /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/main.py"]
