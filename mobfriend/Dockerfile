# FROM ghcr.io/rust-lang/rust:nightly as builder
# WORKDIR /app
# RUN git clone https://github.com/mobilecoinofficial/auxin && cd auxin && git checkout 0.1.12
# WORKDIR /app/auxin
# RUN rustup default nightly
# RUN cargo +nightly build --release

FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:latest as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update && apt install -y python3.9 wget libfuse2 kmod
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

ENV SIGNAL="signal-cli"
COPY --from=signal /usr/bin/signal-cli /signal-version /app/
# COPY --from=builder /app/auxin/target/release/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./mobfriend.py ./scan.py ./template.png /app/
ENTRYPOINT ["/usr/bin/python3.9", "/app/mobfriend.py"]
