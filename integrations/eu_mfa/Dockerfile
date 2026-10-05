FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

# Package the complete upstream source snapshot without modifying model code.
ARG EUMFA_COMMIT=87e94f42d36b2487b16c74b1d81372994a6a41e7
ADD https://github.com/wupperinst/transience-eu-mfa/archive/${EUMFA_COMMIT}.tar.gz /tmp/eu-mfa.tar.gz

WORKDIR /opt/eu-mfa
RUN tar -xzf /tmp/eu-mfa.tar.gz --strip-components=1 -C /opt/eu-mfa \
    && rm /tmp/eu-mfa.tar.gz \
    && python -m pip install --no-cache-dir -r requirements_with_versions.txt \
    && mkdir -p data/baseline_buildings/output \
    && chgrp -R 0 data/baseline_buildings/output \
    && chmod -R g=u data/baseline_buildings/output

LABEL org.opencontainers.image.source="https://github.com/TRANSIENCE-MIC3/MIC3-API" \
      io.mic3.upstream.source="https://github.com/wupperinst/transience-eu-mfa" \
      io.mic3.upstream.revision="${EUMFA_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HOME=/tmp

USER 1001:0
CMD ["python", "eumfa_buildings.py"]
