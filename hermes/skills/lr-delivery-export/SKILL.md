---
name: lr-delivery-export
description: "Prepare and export finished photos with darkroom_ai: choose size and quality for web, social, print proofs or client galleries, name files sensibly, check before export, and say where files went. Process knowledge only."
version: 1.0.0
author: Darkroom
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [photo, lightroom, export, delivery, web, knowledge]
    related_skills: [lr-edit-workflow, lr-straighten-crop, lr-travel-blog, lr-batch-operations, lr-catalog-organisation, lr-safe-operations]
    uses_tools: [get_preview, analyze_photo, export_for_delivery, get_library_state]
---

# Delivery: the last check, then a named file in the exports folder

`export_for_delivery` makes a new sRGB JPEG from the photo as it is now, in the exports folder from the
configuration. It never overwrites; a name already in use gets a number added. It adds no sharpening, watermark or
metadata work, and offers only JPEG up to the maximum size of 4096 pixels on the long side.

## Choose by destination
- **Web and blog:** around the width the page shows; larger only for hero images. Quality high but not maximal.
- **Social:** the platform's shape (see lr-straighten-crop for the crop) and a moderate size.
- **Client proofs and galleries:** moderate size, consistent naming, only picked and edited photos.
- **Print or large display:** the largest available size, highest quality. Say that the tool's limit may be smaller
  than the print needs and that a full-resolution export belongs in Lightroom's own export dialog.

## Before exporting
Check with `get_library_state` that the photos to deliver are the picked or rated ones, and that nothing is flagged
rejected. Look at the final picture with `get_preview` and at its clipping with `analyze_photo`. Make sure the crop
and edits are final: a delivered file does not change when Lightroom changes.

## Naming
Names use letters, digits, spaces, dots, dashes and underscores only; no folders. Use a pattern the person can sort:
event or client, date, running number. For a set, keep the same pattern and a running number.

## Many photos
Ask the person before exporting a large set, and say how many files and where they will go. Export in order and
report any failure by name. Do not delete or move anything.

## Report
Give the file names and the exports folder, the size in pixels and in megabytes, and what was not done (no
sharpening, no watermark, JPEG only).
