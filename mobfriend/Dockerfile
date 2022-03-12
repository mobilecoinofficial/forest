FROM ghcr.io/rust-lang/rust:nightly as builder
WORKDIR /app
RUN git clone https://github.com/mobilecoinofficial/auxin && cd auxin && git checkout 0.1.11
WORKDIR /app/auxin
RUN rustup default nightly
RUN cargo +nightly build --release

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

COPY --from=builder /app/auxin/target/release/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./forest/ /app/forest/
COPY ./mc_util/ /app/mc_util/
COPY ./mobfriend.py ./scan.py ./template.png /app/
ENTRYPOINT ["/usr/bin/python3.9", "/app/mobfriend.py"]
