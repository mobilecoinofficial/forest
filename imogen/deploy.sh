#!/bin/bash
set -o xtrace
# cp -r ../forest forest
# rm fly.toml
# ln -s ren-fly.toml fly.toml
cd $(git rev-parse --show-toplevel)
fly deploy --strategy immediate --dockerfile imogen.Dockerfile --config imogen/ren-fly.toml
