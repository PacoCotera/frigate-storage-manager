FROM python:3.12.12-slim-bookworm
ARG BUILD_VERSION=0.1.0
ARG BUILD_ARCH=amd64
LABEL io.hass.name="Frigate Storage Manager" \
      io.hass.description="Frigate storage validation and read-only cleanup previews" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY fsm ./fsm
CMD ["python", "-m", "fsm"]
