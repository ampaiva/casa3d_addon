ARG BUILD_FROM=python:3.11-slim
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.name="Casa 3D" \
  io.hass.description="Planta interativa da casa para Home Assistant" \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.type="addon" \
  io.hass.version="${BUILD_VERSION}"

WORKDIR /app

RUN apk add --no-cache python3 py3-pip

COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

COPY app ./app
COPY assets ./assets
COPY scripts ./scripts
COPY casa3d-controls.json ./casa3d-controls.json

CMD ["python", "-m", "app.position_editor_server", "--host", "0.0.0.0", "--port", "8000"]
