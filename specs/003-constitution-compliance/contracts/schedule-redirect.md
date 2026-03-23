# Contract: Schedule Redirect (new route)

## Route

`GET /schedule`

## Purpose

No-JS fallback for the index form. The HTML form submits `event_id` as a query parameter; this route redirects to the canonical path-based URL.

## Request

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `event_id` | integer | yes | Competition ID from tracktiming.live |

## Response

**HTTP Status**: `303 See Other`

**Location Header**: `/schedule/{event_id}`

## Error Handling

- Missing or non-integer `event_id`: FastAPI validation returns 422
