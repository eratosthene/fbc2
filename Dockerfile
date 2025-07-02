FROM python:3.10-alpine

RUN apk add --virtual .build-dependencies \
            --no-cache \
            build-base \
            linux-headers \
            git

RUN python -m pip install --upgrade pip
COPY ./requirements.txt /app/requirements.txt

RUN python -m pip install --no-cache-dir --upgrade -r /app/requirements.txt

RUN apk del .build-dependencies && rm -rf /var/cache/apk/*

COPY ./config.py /app
COPY ./fbc /app/fbc

ENV STATIC_PATH=/usr/local/lib/python3.10/site-packages/flask_appbuilder/static
ENV LOG_LEVEL=info

WORKDIR /app
CMD [ "gunicorn", "-w", "4", "--timeout", "300", "--bind", "0.0.0.0:20000", "fbc:app" ]
EXPOSE 20000
