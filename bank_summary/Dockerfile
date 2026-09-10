ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src

# cryptography/pydantic-core pull in Rust builds on musl unless a matching wheel exists;
# .build-deps is removed again once pip install has resolved/built everything it needs.
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev openssl-dev cargo \
    && pip3 install --no-cache-dir . \
    && apk del .build-deps

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
