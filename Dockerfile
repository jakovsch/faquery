FROM alpine:3

RUN apk add --no-cache \
    python3 py3-pip

RUN adduser -Du 1000 bot && \
    mkdir /data && \
    chown 1000:1000 /data

USER 1000:1000

COPY requirements.txt /

RUN pip install -r requirements.txt

COPY faquery /faquery

WORKDIR /
ENTRYPOINT ["python", "-m", "faquery"]
