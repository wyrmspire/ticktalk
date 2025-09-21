FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

# Python 3.11
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.11 python3.11-venv python3.11-distutils curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Make python3 point to 3.11
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
