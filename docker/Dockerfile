# syntax=docker/dockerfile:1

# Extra root CAs for the image, for a network proxy that re-signs HTTPS. Empty
# here, so no other build changes. docker/compose.dev.yml passes a folder with
# the same name as a build context to replace it (see docker/dev-certs).
FROM scratch AS extra-ca

# The base for both stages below, with the extra CAs trusted by the system and
# by pip. A no-op, and an empty layer, when there are none.
FROM python:3.12-alpine AS python-base
RUN --mount=type=bind,source=docker/trust-extra-ca.sh,target=/trust-extra-ca.sh \
    --mount=type=bind,from=extra-ca,target=/extra-ca \
    /trust-extra-ca.sh

# Turn Pipfile.lock into a requirements file for pip, so the lock is the only
# list of versions to keep up to date. Pipenv stays in this stage, out of the
# image.
FROM python-base AS requirements
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install pipenv
WORKDIR /lock
COPY ./Pipfile ./Pipfile.lock ./
RUN pipenv requirements > requirements.txt

FROM python-base

COPY --from=requirements /lock/requirements.txt /app/requirements.txt

# The time zone database, for zoneinfo (fbc/localtime.py). Alpine has none.
RUN apk add --no-cache tzdata

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

# Again, now that certifi is installed. A no-op without extra CAs.
RUN --mount=type=bind,source=docker/trust-extra-ca.sh,target=/trust-extra-ca.sh \
    --mount=type=bind,from=extra-ca,target=/extra-ca \
    /trust-extra-ca.sh

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
