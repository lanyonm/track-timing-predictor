# UI Component Contracts: DaisyUI Frontend Upgrade

**Branch**: `005-daisyui-frontend-upgrade` | **Date**: 2026-03-31

## Design Reference

Approved prototypes in `docs/`:

| File | Covers |
|---|---|
| `daisyui-index-prototype.html` | Landing page (base + index templates) |
| `daisyui-schedule-prototype.html` | Schedule view (base + schedule + _schedule_body templates) |
| `daisyui-palmares-prototype.html` | Palmares profile (base + palmares templates) |
| `daisyui-defaults-prototype.html` | Default durations (base + defaults templates) |
| `daisyui-learned-prototype.html` | Learned durations (base + learned templates) |

## Component Vocabulary

All templates MUST use these DaisyUI class patterns. No page-specific one-off styling.

### Layout

- **Navbar**: `navbar bg-base-100 sticky top-0 z-50 border-b border-base-300 shadow-sm`
- **Page background**: `bg-base-200` on `<body>`
- **Content width**: `max-w-screen-xl mx-auto px-4` (schedule uses `max-w-4xl`; forms use `max-w-md`)

### Cards

- **Standard card**: `card bg-base-100 shadow-md`
- **Card body**: `card-body` (or `card-body p-0` when using custom internal layout)

### Forms

- **Input**: `input input-bordered` (default size for schedule racer form; `w-full` for card forms)
- **Button primary**: `btn btn-primary`
- **Button ghost**: `btn btn-ghost btn-xs`
- **Loading button**: Inline `loading loading-spinner loading-sm` element + text, toggled via CSS class

### Tables

- **Standard table**: `table table-sm` inside a `card` with `overflow-x-auto`
- **Schedule table**: `table table-sm schedule-table` (triggers mobile card transform via custom CSS)

### Badges

- **Delay/ahead (session header)**: `badge badge-outline border-error/40 text-error/80 px-3 py-2.5` / `border-success/40 text-success/80`
- **Racer heat**: `badge badge-outline border-info/40 text-info/80 text-xs px-2 py-1`

### Alerts (Racer Messages)

- **Info (positive match)**: `alert border border-info/20 bg-info/5 text-sm py-2`
- **Warning (caution)**: `alert border border-warning/20 bg-warning/5 text-sm py-2`

### Action Buttons (Event Status Column)

- **Live**: `btn btn-xs btn-outline border-error/30 text-error/80 font-bold`
- **Results**: `btn btn-xs btn-outline border-success/30 text-success/70`
- **Audit**: `btn btn-xs btn-outline border-warning/30 text-warning/70`
- **Start List**: `btn btn-xs btn-outline border-info/30 text-info/70`

### Interactive Components

- **Theme toggle**: `swap swap-rotate btn btn-ghost btn-sm btn-circle` with sun/moon SVGs
- **Collapsible session**: `collapse collapse-arrow bg-base-100 shadow-md border border-base-300`
- **Learned toggle**: `toggle toggle-sm toggle-primary`
- **Modal**: `<dialog class="modal">` with `modal-box`, `modal-action`, `modal-backdrop`
- **Toast**: `toast toast-end toast-bottom` with `alert alert-success`
- **Loading spinner**: `loading loading-spinner loading-xs` (refresh indicator), `loading-sm` (button)

### Row States (Schedule)

- **Completed**: `status-completed` class (custom CSS: `opacity: 0.45`, strikethrough on first `td`)
- **Active**: `active-row bg-warning/10`
- **Upcoming**: `bg-success/5` with `font-medium`
- **Not ready**: `opacity-50`
- **Racer match**: `racer-row bg-info/5`
