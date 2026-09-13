# Playwright's own image ships Chromium plus every system library it needs
# (fonts, libnss3, libatk, etc.) already installed and version-matched to the
# playwright pip package below - this is what `playwright install --with-deps`
# does by hand on a plain base image, pre-baked. Keep this tag's version in
# sync with the playwright== pin in requirements.txt.
#
# -noble (Ubuntu 24.04), not -jammy (22.04): jammy bundles Python 3.10, and
# numpy==2.4.2 (and most of this project's other pins) require Python >=3.11.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

# curl fetches the connectome feathers at container startup only in the
# fallback path (render-entrypoint.sh) - the normal path bakes build/graph.npz
# straight into the image below and never touches the network for it.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x render-entrypoint.sh

# Render sets $PORT itself; this is just documentation for `docker run` elsewhere.
EXPOSE 4663

CMD ["./render-entrypoint.sh"]
