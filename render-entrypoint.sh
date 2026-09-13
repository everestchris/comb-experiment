#!/bin/bash
# Runs once per container start on Render.
#
# build/graph.npz (~36MB) is committed to the repo and baked into the image
# by `COPY . .` in the Dockerfile, so there is nothing to download or build
# here in the normal case - no persistent disk, no 1.1GB fetch, no running
# build_graph.py's memory-hungry pandas/pyarrow pass on a small container.
#
# The fallback below only matters if graph.npz is ever missing from the
# image (e.g. building from a checkout that stripped it back out). It never
# invents weights: if the download or build fails, the container fails to
# start, same as running locally with a missing build/graph.npz - see
# hive.py's resolve_graph_path() and scripts/fetch_graph.md.
set -euo pipefail

if [ ! -f /app/build/graph.npz ]; then
  echo "build/graph.npz missing from the image - fetching the three FlyEM"
  echo "male-CNS v1.0 feathers (CC-BY, ~1.1GB total) and building it now..."
  mkdir -p /app/data /app/build
  BASE="https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome"
  curl -fL --retry 3 -o /app/data/connectome-weights.feather \
    "$BASE/connectome-weights-male-cns-v1.0-minconf-0.5.feather"
  curl -fL --retry 3 -o /app/data/body-neurotransmitters.feather \
    "$BASE/body-neurotransmitters-male-cns-v1.0.feather"
  curl -fL --retry 3 -o /app/data/body-annotations.feather \
    "$BASE/body-annotations-male-cns-v1.0-minconf-0.5.feather"

  python build_graph.py

  rm -f /app/data/*.feather
fi

exec python roam_hive.py --host 0.0.0.0 --port "${PORT:-4663}"
