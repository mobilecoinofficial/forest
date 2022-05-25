FROM ubuntu:hirsute as auxin
WORKDIR /app
RUN apt-get update && apt-get -yy install curl unzip
ENV A=B
RUN curl -L --output auxin-cli.zip https://nightly.link/mobilecoinofficial/auxin/workflows/actions/main/auxin-cli.zip
RUN unzip auxin-cli.zip && chmod +x ./auxin-cli

FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:v0-10-5-5 as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv /app/venv
COPY ./pyproject.toml ./poetry.lock /app/
COPY ./forest/ /app/forest/
COPY ./mc_util/ /app/mc_util/
RUN touch /app/README.md
RUN VIRTUAL_ENV=/app/venv poetry install --no-dev

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update && apt install -y python3.9 wget libfuse2 kmod
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

COPY --from=signal /usr/bin/signal-cli /signal-version /app/
COPY --from=auxin /app/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./mobfriend.py ./scan.py ./template.png /app/
COPY ./forest/ /app/forest/
COPY ./mc_util/ /app/mc_util/
ENTRYPOINT ["/usr/bin/python3.9", "/app/mobfriend.py"]
