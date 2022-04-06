FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:v0-10-2-6 as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

FROM bitnami/kubectl:1.21.8 as kubectl

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt-get update
RUN apt-get install -y python3.9 wget libfuse2 kmod jq unzip ssh
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/
COPY --from=signal /usr/bin/signal-cli /signal-version /app/
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY --from=kubectl /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/
COPY ./mc_util/ /app/mc_util/
COPY ./forest/ /app/forest/
COPY ./gelato.py ./imogen.py ./free-imagegen-job.yaml ./paid-imagegen-job.yaml ./a6000.yaml ./esrgan-job.yaml /app/ 
ENTRYPOINT ["/usr/bin/python3.9", "/app/imogen.py"]
