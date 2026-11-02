# syntax=docker/dockerfile:1

# Turn Pipfile.lock into a requirements file for pip, so the lock is the only
# list of versions to keep up to date. Pipenv stays in this stage, out of the
# image.
FROM python:3.12-alpine AS requirements
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install pipenv
WORKDIR /lock
COPY ./Pipfile ./Pipfile.lock ./
RUN pipenv requirements > requirements.txt

FROM python:3.12-alpine

COPY --from=requirements /lock/requirements.txt /app/requirements.txt

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

# config.py isn't copied in: it holds the live credentials, so the compose
# files mount it instead, and a change to it needs no rebuild.
COPY ./fbc /app/fbc
COPY ./migrations /app/migrations
COPY --chmod=755 ./docker/docker-entrypoint.sh /app/docker-entrypoint.sh

ENV STATIC_PATH=/usr/local/lib/python3.12/site-packages/flask_appbuilder/static
ENV LOG_LEVEL=info
ENV FLASK_APP=fbc

WORKDIR /app
ENTRYPOINT [ "/app/docker-entrypoint.sh" ]
CMD [ "gunicorn", "-w", "4", "--timeout", "300", "--bind", "0.0.0.0:20000", "fbc:app" ]
EXPOSE 20000
