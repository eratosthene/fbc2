# syntax=docker/dockerfile:1
FROM python:3.10-alpine

COPY ./requirements.txt /app/requirements.txt

# Install, build and remove the build dependencies in one layer so they don't
# end up in the image. The cache mount keeps pip's downloads between builds.
RUN --mount=type=cache,target=/root/.cache/pip \
    apk add --no-cache --virtual .build-dependencies \
            build-base \
            linux-headers \
            git \
    && python -m pip install --upgrade pip \
    && python -m pip install --upgrade -r /app/requirements.txt \
    && apk del .build-dependencies

COPY ./config.py /app
COPY ./fbc /app/fbc
COPY ./migrations /app/migrations
COPY --chmod=755 ./docker-entrypoint.sh /app/docker-entrypoint.sh

ENV STATIC_PATH=/usr/local/lib/python3.10/site-packages/flask_appbuilder/static
ENV LOG_LEVEL=info
ENV FLASK_APP=fbc

WORKDIR /app
ENTRYPOINT [ "/app/docker-entrypoint.sh" ]
CMD [ "gunicorn", "-w", "4", "--timeout", "300", "--bind", "0.0.0.0:20000", "fbc:app" ]
EXPOSE 20000
