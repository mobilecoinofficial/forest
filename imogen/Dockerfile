FROM ghcr.io/graalvm/graalvm-ce:java17-21.3.0 as sigbuilder
ENV GRAALVM_HOME=/opt/graalvm-ce-java17-21.3.0/ 
SHELL ["/usr/bin/bash", "-c"]
WORKDIR /app
RUN microdnf install -y git zlib-devel && rm -rf /var/cache/yum
RUN gu install native-image
RUN git clone --branch forest-fork https://github.com/forestcontact/signal-cli
WORKDIR /app/signal-cli
RUN git pull origin forest-fork-v2.0.0
RUN git log -1 --pretty=%B | tee commit-msg
RUN ./gradlew nativeCompile

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
ENV a=1
RUN apt-get update
RUN apt-get install -y python3.9 wget libfuse2 kmod jq unzip ssh
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

# v5.12.2 for fly.io
RUN wget -q -O awscli.zip "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" && unzip awscli.zip && rm awscli.zip && ./aws/install
RUN wget -q -O fuse.ko "https://public.getpost.workers.dev/?key=01F54FQVAX85R1Y98ACCXT2AGT&raw"
#RUN sudo insmod fuse.ko
COPY --from=sigbuilder /app/signal-cli/build/native/nativeCompile/signal-cli /app/signal-cli/commit-msg /app/signal-cli/build.gradle.kts  /app/
# for signal-cli's unpacking of native deps
COPY --from=sigbuilder /lib64/libz.so.1 /lib64
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./mc_util/ /app/mc_util/
COPY ./forest/ /app/forest/
COPY ./imogen.py /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/imogen.py"]
