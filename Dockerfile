FROM searxng/searxng:latest

COPY settings.yml /etc/searxng/settings.yml

ENV SEARXNG_BIND_ADDRESS=0.0.0.0
ENV SEARXNG_PORT=10000

EXPOSE 10000
