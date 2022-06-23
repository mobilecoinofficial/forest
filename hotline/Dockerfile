FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:latest as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM ghcr.io/rust-lang/rust:nightly as builder
WORKDIR /app
RUN rustup default nightly
RUN git clone https://github.com/mobilecoinofficial/auxin && cd auxin && git pull origin 0.1.17
WORKDIR /app/auxin
RUN cargo +nightly build --release

FROM ubuntu:hirsute as libbuilder
WORKDIR /app
ENV TZ=EST
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
RUN DEBIAN_FRONTEND="noninteractive" apt update && apt install -yy python3.9 python3.9-venv libfuse2 pipenv
RUN python3.9 -m venv /app/venv
COPY Pipfile.lock Pipfile /app/
RUN VIRTUAL_ENV=/app/venv pipenv install

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
ENV TZ=EST
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
RUN apt update && apt install -y python3.9 wget libfuse2 kmod
RUN apt-get clean autoclean && apt-get autoremove --yes ; rm -rf /var/lib/{apt,dpkg,cache,log}/

COPY --from=builder /app/auxin/target/release/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY --from=signal /usr/bin/signal-cli /signal-version /app/
COPY --from=signal /lib/x86_64-linux-gnu/libz.so.1 /lib64/

COPY ./forest/ /app/forest/
COPY ./mc_util/ /app/mc_util/
COPY ./captcha/ /app/captcha/
COPY ./hotline.py /app/
ENTRYPOINT ["/usr/bin/python3.9", "/app/hotline.py"]
