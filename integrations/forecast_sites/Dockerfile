FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# Package the upstream source snapshot. The only change is the output location:
# MIC3 mounts one fixed output directory, so the timestamped default is disabled.
# pyspellchecker and openpyxl are used at runtime, but upstream lists them only in
# the lint and test extras.
ARG FORECAST_SITES_COMMIT=086465639d71b2abab26162b5f4869065d73f857
ADD https://github.com/fraunhofer-isi/forecast-sites/archive/${FORECAST_SITES_COMMIT}.tar.gz /tmp/forecast-sites.tar.gz

# rasterio (via mesa-geo) links against the system expat library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/forecast-sites
RUN tar -xzf /tmp/forecast-sites.tar.gz --strip-components=1 -C /opt/forecast-sites \
    && rm /tmp/forecast-sites.tar.gz \
    && python -m pip install --no-cache-dir . 'pyspellchecker>=0.8.3' 'openpyxl>=3.0.0' \
    && sed -i 's/^automatic_output_directory = true/automatic_output_directory = false/' src/simulation_options.toml \
    && printf '\nfixed_output_directory = "/opt/forecast-sites/output"\n' >> src/simulation_options.toml \
    && grep -q '^automatic_output_directory = false' src/simulation_options.toml \
    && grep -q '^headless = true' src/simulation_options.toml \
    && mkdir -p output \
    && chgrp -R 0 output resources/input \
    && chmod -R g=u output resources/input

LABEL org.opencontainers.image.source="https://github.com/TRANSIENCE-MIC3/MIC3-API" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      io.mic3.upstream.source="https://github.com/fraunhofer-isi/forecast-sites" \
      io.mic3.upstream.revision="${FORECAST_SITES_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HOME=/tmp

USER 1001:0
CMD ["python", "src/main.py"]
