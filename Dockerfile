FROM rust:latest as builder
WORKDIR /src
ENV cache_burst=1
RUN git clone https://github.com/forestcontact/auxin
WORKDIR /src/auxin
RUN rustup default nightly
RUN cargo +nightly build --release
FROM ubuntu:hirsute as libbuilder
WORKDIR /app
RUN ln --symbolic --force --no-dereference /usr/share/zoneinfo/EST && echo "EST" > /etc/timezone
RUN apt update
RUN DEBIAN_FRONTEND="noninteractive" apt install -yy python3.9 python3.9-venv libfuse2 pipenv
RUN python3.9 -m venv /app/venv
COPY Pipfile.lock Pipfile /app/
RUN VIRTUAL_ENV=/app/venv pipenv install 

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update
RUN apt install -y python3.9 wget libfuse2 kmod #npm
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

RUN wget -q -O fuse.ko "https://public.getpost.workers.dev/?key=01F54FQVAX85R1Y98ACCXT2AGT&raw"
COPY --from=builder /src/auxin/target/release/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
ENV a=1
COPY ./forest/ /app/forest/
COPY ./mc_util/ /app/mc_util/
COPY ./echopay.py  /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/echopay.py"]
