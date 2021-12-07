FROM ghcr.io/rust-lang/rust:nightly as builder
WORKDIR /src
RUN git clone https://github.com/forestcontact/auxin && cd auxin && git pull origin 0.1.4
WORKDIR /app
RUN rustup default nightly
# from https://stackoverflow.com/questions/58473606/cache-rust-dependencies-with-docker-build
RUN mkdir -p /app/auxin_cli/src /app/auxin/src
RUN mv /src/auxin/Cargo.toml .
RUN mv /src/auxin/auxin/Cargo.toml ./auxin
RUN mv /src/auxin/auxin_cli/Cargo.toml /app/auxin_cli/
RUN mv /src/auxin/auxin_protos /app/auxin_protos
RUN mv /app/auxin_protos/build.rs.always /app/auxin_protos/build.rs
WORKDIR /app/auxin_cli
# build dummy auxin_cli using latest Cargo.toml/Cargo.lock
RUN echo 'fn main() { println!("Dummy!"); }' > ./src/lib.rs
RUN echo 'fn lib() { println!("Dummy!"); }' > ../auxin/src/lib.rs
RUN find /app/
RUN cargo build --release
RUN rm -r /app/auxin/src /app/auxin_cli/src
RUN mv /src/auxin/auxin/src /app/auxin/src
RUN mv /src/auxin/auxin/data /app/auxin/data
RUN mv /src/auxin/auxin_cli/src /app/auxin_cli/src
RUN find /app/auxin_cli
RUN touch -a -m /app/auxin_cli/src/main.rs
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

COPY --from=builder /app/target/release/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./forest/ /app/forest/
COPY ./mc_util/ /app/mc_util/
COPY ./mobfriend.py  /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/mobfriend.py"]
