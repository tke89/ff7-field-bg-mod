#!/bin/bash
# Install the FFNx-3d build over the game's FFNx (DLL as AF3DN.P, its .pdb, compiled shaders). Leaves FFNx.toml and
# every config alone. The user's original FFNx 1.24.3 is backed up in workingdir/_backup_ffnx_1.24.3 (manifest.json).
set -e
G="/c/Program Files (x86)/Steam/steamapps/common/FINAL FANTASY VII Steam Edition/ff7/workingdir"
B="${FFNX3D:-$(cd "$(dirname "$0")/../../.." && pwd)/FFNx-3d}/.build/bin"  # FFNx-3d beside the Studio repo
[ -f "$B/FFNx.dll" ] || { echo "no build at $B"; exit 1; }
cp "$B/FFNx.dll" "$G/AF3DN.P"
[ -f "$B/FFNx.pdb" ] && cp "$B/FFNx.pdb" "$G/FFNx.pdb"
cp "$B/shaders/"* "$G/shaders/"
echo "installed FFNx-3d ($(ls "$B/shaders" | wc -l) shaders)"
