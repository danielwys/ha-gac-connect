# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Home Assistant custom integration in Python, backed by the published `gac-connect` library.
The optional dashboard cards are Lit and TypeScript web components. The integration ships
their main bundle and page loader. In storage mode it registers the main bundle as a versioned
dashboard resource. In YAML mode it uses the page loader. CI tests the pinned library and checks
that the committed frontend assets match the source.

## Users

Home Assistant users who own a GAC vehicle (AION, Hyptec, Trumpchi/GS, M8/GN8 and any
other model GAC's app serves, in any of the integration's 22 regions), using their own GAC
account. They glance at the car from a desktop dashboard (three columns), a wall tablet or
a phone (one column), and act on it remotely: warm or cool the cabin, lock it, start or
schedule a charge, find it.

Secondary: the owner as the integration's administrator, adjusting permissions, tracking,
quiet hours, polling and which entities appear.

## Product Purpose

Six narrow Lovelace cards, each with one job, that let an owner monitor and control their
car without wiring entities by hand: Vehicle, Climate, Charging, Location, Activity and
Settings. A user picks the car once in each card's editor; the card finds that car's
entities itself. Success is a dashboard an owner trusts at a glance and can act from
without opening Home Assistant's entity dialogs.

## Positioning

The integration uses the car's capability list and reports to decide which entities and
controls to show. Commands show the requested state, then whether the car confirmed it,
refused it with a reason or timed out.

## Operating Context

- Cards sit in Home Assistant dashboards on desktop browsers, wall tablets and Companion
  apps. The layout adapts to the card width. Controls wrap and the optional vehicle photo
  keeps its aspect ratio.
- Card surfaces, text, fonts and corners follow the user's Home Assistant theme.
- Commands go through GAC's cloud: the car's answer takes up to about 10 seconds;
  confirmation from the car's next report can take up to 3 minutes.
- Readings arrive by polling (default every 5 minutes, paused in quiet hours) or GAC's push
  feed, so data can be minutes old. Last report, Data freshness and Polling status say how
  old and why.

## Capabilities and Constraints

- Entities exist only where the car's capability list (or its reports) says so; Always show
  and Omit choices override. Cards must render well with any subset missing.
- Entities are found by device and translation key (unique ID is `<VIN>_<key>`), never by
  entity ID, so renamed entities keep working.
- Unlock, charging connector unlock and vehicle shutdown exist only when their Allow
  options are on. Location is off by default (parked and moving are separate choices).
- No remote stop-charging command (unlocking the connector stops a charge); no engine
  start; PIN commands are refused.
- A shared request budget: at most 6 commands a minute and 60 an hour. A held-back command
  fails with an error.
- Changing an entity's visibility reloads the integration.
- Vehicle actions use Home Assistant permissions for the selected vehicle and command
  feature. Administrators manage options and entity visibility.
- The Aion V lock reading can use the front lock reports, closed doors and armed alarm
  when both rear lock codes are unreported. The dashboard keeps lock, alarm and openings separate.
- Body styles to draw: hatch/crossover, SUV, coupé-SUV and MPV with sliding rear doors;
  PHEVs as well as EVs.
- Terminology follows the integration (British English): Boot, Bonnet, Tyre, Charge now,
  Scheduled charging, Smart battery temp, Sterilisation, Pre-conditioning run time; command
  outcomes Requested state reported, Vehicle rejected request, Not confirmed in time.

## Brand Commitments

- Name: GAC Connect (unofficial, beta). Not affiliated with or endorsed by GAC; GAC, AION
  and Hyptec are trademarks of their owners. No GAC logos, renders or 3D assets ship with
  the project.
- Car imagery is original line art drawn for the project, per body style, plus an optional
  photo the owner supplies.
- General instructions apply across models. Model-specific readings are documented where
  they affect what the integration displays.

## Evidence on Hand

- The full entity list and behaviour in `README.md` and `custom_components/gac_connect`.
- `models/` holds GAC's showroom scenes and renders recovered for research. They are
  git-ignored and must not ship or be traced.
- The development demo uses labelled synthetic data. The fork notes record which features
  were also verified on a live installation.

## Product Principles

1. Show only what this car has; a missing capability leaves no gap, no greyed-out control.
2. Be honest about time: stale readings and commands in flight are always visible.
3. Make consequential actions deliberate: unlocking, opening the boot, shutting the car
   down and releasing the connector take a held press, never a stray tap.
4. One choice configures a card: pick the car, and everything else is found.
5. Every GAC model is first-class.

## Accessibility & Inclusion

Touch targets at least 44 px for wall tablets and phones; full keyboard use and visible
focus; every control and state named for screen readers; state never carried by colour
alone; reduced-motion respected; contrast holds in both light and dark themes.
