FROM registry.gitlab.com/packaging/signal-cli/signal-cli-jre:v0-10-2-5 as signal
#FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:v0-10-5-3 as signal
RUN signal-cli --version | tee /signal-version
#RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli
#RUN mv $(which signal-cli) /usr/bin/signal-cli

# FROM ghcr.io/graalvm/graalvm-ce:java17-21.3.0 as signal
# ENV GRAALVM_HOME=/opt/graalvm-ce-java17-21.3.0/ 
# SHELL ["/usr/bin/bash", "-c"]
# WORKDIR /app
# RUN microdnf install -y git zlib-devel && rm -rf /var/cache/yum
# RUN gu install native-image
# RUN git clone https://github.com/AsamK/signal-cli
# WORKDIR /app/signal-cli
# RUN git log -1 --pretty=%B | tee signal-version
# RUN ./gradlew nativeCompile

# FROM ubuntu:hirsute as auxin
# WORKDIR /app
# RUN apt-get update && apt-get -yy install curl unzip
# RUN curl -L --output auxin-cli.zip https://nightly.link/mobilecoinofficial/auxin/workflows/actions/main/auxin-cli.zip
# RUN unzip auxin-cli.zip && chmod +x ./auxin-cli

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

FROM bitnami/kubectl:1.21.8 as kubectl

FROM ubuntu:focal 
WORKDIR /app
RUN mkdir -p /app/data
RUN apt-get update \ 
    && DEBIAN_FRONTEND="noninteractive" apt-get install -y python3.9 libfuse2 kmod jq ssh squid apache2-utils openjdk-17-jre-headless \
    && apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/
COPY --from=signal /usr/bin/signal-cli /signal-version /app/
COPY --from=signal /usr/share/signal-cli /usr/share/signal-cli
#COPY --from=signal /app/signal-cli/build/native/nativeCompile/signal-cli /app/
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
#COPY --from=auxin /app/auxin-cli /app/
COPY --from=kubectl /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/
COPY ./mc_util/ /app/mc_util/
COPY ./forest/ /app/forest/
COPY ./gelato.py ./imogen.py ./free-imagegen-job.yaml ./paid-imagegen-job.yaml ./a6000.yaml ./esrgan-job.yaml ./diffuse.yaml ./dalle.yaml /app/
COPY ./squid.conf ./passwd /etc/squid/
ENTRYPOINT ["/usr/bin/python3.9", "/app/imogen.py"]
