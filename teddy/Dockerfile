FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:latest as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM ubuntu:hirsute as auxin
WORKDIR /app
RUN apt-get update && apt-get -yy install curl unzip
RUN curl -L --output auxin-cli.zip https://nightly.link/mobilecoinofficial/auxin/workflows/actions/main/auxin-cli.zip
RUN unzip auxin-cli.zip && chmod +x ./auxin-cli

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install --no-dev

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
ENV TZ=EST
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
RUN apt update && apt install -y python3.9 wget libfuse2 kmod
RUN apt-get clean autoclean && apt-get autoremove --yes ; rm -rf /var/lib/{apt,dpkg,cache,log}/

COPY --from=auxin /app/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY --from=signal /usr/bin/signal-cli /signal-version /app/
COPY --from=signal /lib/x86_64-linux-gnu/libz.so.1 /lib64/

# COPY ./forest/ /app/forest/
# COPY ./mc_util/ /app/mc_util/
COPY ./how-to-activate.gif ./how-to-donate.gif /app/
COPY ./charity.py /app/
ENTRYPOINT ["/usr/bin/python3.9", "/app/charity.py"]
