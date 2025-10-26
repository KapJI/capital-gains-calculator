#!/usr/bin/env bash

# Generate cropped PNG preview for README from the first page of the example PDF.
# Requires: ImageMagick (with Ghostscript).
# Usage: run with no arguments.

set -euo pipefail

### --- Configuration ---------------------------------------------------------

# Source PDF and output PNG paths
PDF_PATH="docs/example_report.pdf"
OUTPUT_PATH="docs/example_report_preview.png"

# Rendering resolution (DPI)
DPI=300

# Height (in pixels) of the top strip to keep after rendering.
# Width is taken as full page width automatically.
CROP_HEIGHT=2020

# Margin (in pixels) to add after trimming.
BORDER=20

# Resize after cropping
FINAL_WIDTH=1200

### ---------------------------------------------------------------------------

# Change to project root
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "Generating preview from '$PDF_PATH' → '$OUTPUT_PATH'"
echo "Density: ${DPI} DPI, Crop: full-width x ${CROP_HEIGHT}, Trim: on"

# Pipeline:
# 1) Render first page at DPI
# 2) Crop full width, top CROP_HEIGHT pixels
# 3) Trim uniform border
# 4) Add optional white border margin
# 5) Flatten onto white to remove transparency
# 6) Resize to look good in README
# 7) Write optimised PNG
magick -density "$DPI" "$PDF_PATH[0]" -units PixelsPerInch -strip miff:- |
    magick miff:- \
        -gravity North -crop "x${CROP_HEIGHT}+0+0" +repage \
        -trim +repage \
        -bordercolor white -border "${BORDER}" \
        -background white -alpha remove -alpha off \
        -resize "${FINAL_WIDTH}" \
        -define png:compression-level=9 \
        -define png:compression-strategy=2 \
        -define png:exclude-chunk=all \
        -strip "$OUTPUT_PATH"

echo "✅ Preview generated: $OUTPUT_PATH"
echo "👀 Check if it looks good or adjust CROP_HEIGHT in the script."
