ARG BUILD_FROM
FROM $BUILD_FROM

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install requirements for add-on
RUN \
    apk add --no-cache \
        python3 \
        py3-pip \
    && pip3 install --no-cache-dir \
        fastapi \
        uvicorn \
        aiohttp \
        requests \
        syncedlyrics

# Copy data for add-on
COPY . /app
WORKDIR /app

# Run the app
RUN chmod a+x /app/run.sh

CMD [ "/app/run.sh" ]
