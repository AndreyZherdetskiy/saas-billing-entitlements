FROM grafana/grafana:11.5.2

COPY deploy/observability/grafana/provisioning /etc/grafana/provisioning
# Empty dirs Grafana expects; do not COPY .gitkeep into the image.
RUN mkdir -p /etc/grafana/provisioning/plugins /etc/grafana/provisioning/alerting \
    && rm -f /etc/grafana/provisioning/plugins/.gitkeep \
            /etc/grafana/provisioning/alerting/.gitkeep
