# Data Model: DaisyUI Frontend Upgrade

**Branch**: `005-daisyui-frontend-upgrade` | **Date**: 2026-03-31

## Overview

This feature makes no changes to the application's data model. All changes are purely presentational (Jinja2 templates + CSS).

## New Cookie

| Name | Values | Default | Persistence | Purpose |
|---|---|---|---|---|
| `theme` | `light`, `dark` | Not set (falls back to system preference) | 1 year (`max-age=31536000`) | Stores user's manual theme override (FR-008) |

This cookie is set client-side via JavaScript. No backend route is needed. It follows the same pattern as the existing `racer_name` and `use_learned` cookies.

## No Other Changes

- No new database tables or columns
- No new Pydantic models
- No changes to DynamoDB or SQLite schemas
- No new API request/response shapes
