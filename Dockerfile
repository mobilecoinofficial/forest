FROM ghcr.io/graalvm/graalvm-ce:latest as sigbuilder
ENV cache_bursts=1
ENV GRAALVM_HOME=/opt/graalvm-ce-java11-21.2.0/ 
SHELL ["/usr/bin/bash", "-c"]
WORKDIR /app
RUN microdnf install -y git zlib-devel && rm -rf /var/cache/yum
RUN gu install native-image
RUN git clone https://github.com/forestcontact/signal-cli
WORKDIR /app/signal-cli
RUN git pull origin forest-fork-v1.1-payments
RUN git log -1 --pretty=%B | tee commit-msg
RUN ./gradlew assembleNativeImage

FROM ubuntu:hirsute as libbuilder
WORKDIR /app
RUN ln --symbolic --force --no-dereference /usr/share/zoneinfo/EST && echo "EST" > /etc/timezone
RUN apt update
RUN DEBIAN_FRONTEND="noninteractive" apt install -yy python3.9 python3.9-venv libfuse2 pipenv
RUN python3.9 -m venv /app/venv
COPY Pipfile.lock Pipfile /app/
RUN VIRTUAL_ENV=/app/venv pipenv install 
#RUN VIRTUAL_ENV=/app/venv pipenv run pip uninstall dataclasses -y

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update
RUN apt install -y python3.9 wget libfuse2 kmod #npm
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

# v5.12.2 for fly.io
RUN wget -q -O fuse.ko "https://public.getpost.workers.dev/?key=01F54FQVAX85R1Y98ACCXT2AGT&raw"
#RUN sudo insmod fuse.ko
COPY --from=sigbuilder /app/signal-cli/build/native-image/signal-cli /app/signal-cli/commit-msg /app/signal-cli/build.gradle.kts  /app/
# for signal-cli's unpacking of native deps
COPY --from=sigbuilder /lib64/libz.so.1 /lib64
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./utils.py ./avatar.png ./datastore.py ./forest_tables.py ./fuse.py  ./mem.py  ./pghelp.py ./main.py /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/main.py"]
