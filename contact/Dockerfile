FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:v0-10-4-1 as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv --without-pip /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt-get update
RUN apt-get install -y python3.9 libfuse2
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/
COPY --from=signal /usr/bin/signal-cli /signal-version /app/
# for signal-cli's unpacking of native deps
COPY --from=signal /lib/x86_64-linux-gnu/libz.so.1 /lib64/
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY  ./teli.py ./forest_tables.py ./contactbot.py ./avatar.png /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/contactbot.py"]
