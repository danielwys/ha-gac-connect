---
name: GAC Connect cards
description: Vehicle dashboard cards that follow the Home Assistant theme.
colors:
  land: "var(--ha-card-background, var(--card-background-color))"
  paper: "var(--secondary-background-color)"
  water: "var(--primary-background-color)"
  ink: "var(--primary-text-color)"
  muted: "var(--secondary-text-color)"
  hair: "var(--divider-color)"
  edge: "var(--ha-card-border-color, var(--divider-color))"
  motorway: "#2e6db4"
  motorway-case: "#1b3f6b"
  motorway-ink: "#ffffff"
  primary-green: "#1f8a4c"
  a-road-red: "#d2352b"
  b-road-yellow: "#f2c230"
  minor-road: "var(--secondary-background-color)"
  minor-road-case: "var(--disabled-text-color)"
  plan-body: "var(--secondary-background-color)"
  plan-glass: "color-mix(in srgb, var(--primary-color) 12%, var(--gac-land))"
typography:
  display:
    fontFamily: "var(--ha-font-family-body, Roboto, Noto, sans-serif)"
    fontSize: "40px"
    fontWeight: 400
    lineHeight: 1
    fontFeature: "tnum, lnum"
  headline:
    fontFamily: "var(--ha-font-family-heading, var(--ha-font-family-body, Roboto, Noto, sans-serif))"
    fontSize: "var(--ha-card-header-font-size, 24px)"
    fontWeight: 400
    lineHeight: 1.2
  control:
    fontFamily: "var(--ha-font-family-body, Roboto, Noto, sans-serif)"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.3
  body:
    fontFamily: "var(--ha-font-family-body, Roboto, Noto, sans-serif)"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "var(--ha-font-family-body, Roboto, Noto, sans-serif)"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: "16px"
rounded:
  track: "1px"
  control: "2px"
  action: "var(--ha-border-radius-md, 8px)"
  card: "var(--ha-card-border-radius, 12px)"
  junction: "50%"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  plate: "56px"
  target: "48px"
components:
  card:
    backgroundColor: "{colors.land}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    typography: "{typography.body}"
  name-plate:
    textColor: "{colors.ink}"
    typography: "{typography.headline}"
    minHeight: "{spacing.plate}"
    padding: "0 16px"
  action:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.action}"
    typography: "{typography.control}"
    minHeight: "{spacing.target}"
  action-on:
    backgroundColor: "{colors.motorway}"
    textColor: "{colors.motorway-ink}"
  action-pending:
    backgroundColor: "{colors.water}"
    textColor: "{colors.motorway-case}"
  switch-track:
    backgroundColor: "{colors.minor-road}"
    rounded: "{rounded.track}"
    width: "48px"
    height: "16px"
  switch-track-on:
    backgroundColor: "{colors.motorway}"
---

# GAC Connect card design

The cards sit alongside stock Home Assistant cards. Surfaces, borders, shadows,
corner radii, text and typography follow the active theme. The road-style battery
gauge, charge window and car plan show vehicle data within that frame.

[Theme tokens and shared styles](frontend/src/theme.ts) define the defaults and
the light and dark palettes. Blue indicates a control or selection. Green indicates
a healthy or charging state. Yellow draws attention to warnings or openings, and
red marks faults or refusals. Text and shapes identify states alongside colour.

## Layout and typography

- The card header uses Home Assistant's heading font and size. Readings use
  tabular figures where they need to stay steady as values change.
- Cards respond to their own width. The main action grid fits as many 128 px
  columns as it can, with one column available below that width. Actions are at
  least 48 px tall and long labels can wrap.
- At 380 px or less, the Vehicle card stacks the plan and nearby readings.
  At 330 px or less, the shared card layout reduces horizontal padding.
- The optional vehicle image fills the card width at its original aspect ratio.
  The plan drawing beneath it still shows openings and their positions.

## Readings and controls

- The Vehicle card shows the lock state, body alarm and openings separately.
  The lock state comes from the Home Assistant lock entity or lock binary
  sensor. The integration interprets model-specific readings in
  [lock state](custom_components/gac_connect/lock_state.py).
- The battery gauge shows charge and range. The charging window uses a
  24-hour scale and wraps at midnight for overnight windows. The climate scale
  shows cabin, target and outside temperatures.
- Each footer shows the state and age of the latest report. The separate refresh
  button requests a new report. Command states distinguish pending, confirmed,
  rejected and unconfirmed requests.
- Consequential actions such as unlocking, opening windows or the boot, and
  sounding the horn use a 700 ms hold. Buttons provide a visible focus ring.
  Pending and charging animations respect reduced-motion preferences.

## Source and build

[Shared controls](frontend/src/controls.ts) and
[vehicle card](frontend/src/cards/vehicle-card.ts) are the source for component
behavior and layout. See the [frontend build instructions](custom_components/gac_connect/frontend/README.md)
to regenerate the assets.
