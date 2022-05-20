FROM ubuntu:hirsute as auxin
WORKDIR /app
RUN apt-get update && apt-get -yy install curl unzip
ENV A=1
RUN curl -L --output auxin-cli.zip https://nightly.link/mobilecoinofficial/auxin/workflows/actions/main/auxin-cli.zip
RUN unzip auxin-cli.zip && chmod +x ./auxin-cli

FROM registry.gitlab.com/packaging/signal-cli/signal-cli-native:v0-10-5-5 as signal
RUN signal-cli --version | tee /signal-version
RUN mv /usr/bin/signal-cli-native /usr/bin/signal-cli

FROM registry.gitlab.com/packaging/signal-cli/signal-cli-jre:v0-10-5-5 as signal-jre
RUN signal-cli --version | tee /signal-version

FROM python:3.9 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.9 -m venv /app/venv 
COPY ./pyproject.toml /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

FROM ubuntu:hirsute
WORKDIR /app
RUN mkdir -p /app/data
RUN apt update && apt install -y python3.9 libfuse2 kmod openjdk-17-jre-headless
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

#ENV SIGNAL="signal-cli"
#COPY --from=signal /usr/bin/signal-cli /signal-version /app/
COPY --from=signal-jre /usr/bin/signal-cli /signal-version /app/
COPY --from=signal-jre /usr/share/signal-cli /usr/share/signal-cli
COPY --from=auxin /app/auxin-cli /app/auxin-cli
COPY --from=libbuilder /app/venv/lib/python3.9/site-packages /app/
COPY ./mobfriend.py ./scan.py ./template.png /app/
ENTRYPOINT ["/usr/bin/python3.9", "/app/mobfriend.py"]
