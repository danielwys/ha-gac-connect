# GAC Connect for Home Assistant (unofficial)

> **Beta. Use at your own risk.** Remote commands physically act on your vehicle
> (A/C, locks, windows, boot, charging). Review what an automation can do before
> letting it touch the car.
>
> Not affiliated with, endorsed by or supported by GAC. GAC and AION are trademarks
> of their respective owners. Use it with a vehicle you own, on your own account.

Monitor and control a **GAC / AION** vehicle from Home Assistant: battery, range,
charging and charge scheduling, climate and seats, doors, windows, boot and
location.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=leecaochang&repository=ha-gac-connect&category=integration)

## Requirements

- Home Assistant 2025.2 or newer.
- A GAC International app account (or My AION in the United Kingdom) and the mobile
  number it uses.

## Install

**HACS:** use the badge above, or:

1. HACS → three-dot menu → **Custom repositories**.
2. Add this repository with category **Integration**.
3. Install **GAC Connect (unofficial, beta)** and restart Home Assistant.

Releases are pre-releases while in beta: enable **Show beta versions** on the
integration's HACS page.

**Manually:** copy `custom_components/gac_connect` into your Home Assistant
`config/custom_components/` folder and restart.

## Set up

**Settings → Devices & services → Add integration → GAC Connect.**

1. Choose your region and enter your mobile number.
2. Solve the slide puzzle that opens (drag the piece into the gap, then Verify;
   arrow keys move it one pixel). A miss shows a new puzzle.
3. Enter the SMS code.
4. Choose the vehicle, and whether to allow remote unlocking and remote shutdown.

Regions: Australia, Brazil, Cambodia, Finland, Greece, Hong Kong, Indonesia,
Israel, Kuwait, Macau, Mexico, New Zealand, Philippines, Poland, Portugal, Russia,
Saudi Arabia, Singapore, Spain, Thailand, United Arab Emirates, United Kingdom.

Signing in here signs the official app out once. Sign the app back in and both keep
working.

## Which entities appear

Entities are matched to the car. At setup the integration reads the car's
capability list from GAC's service, and uses the car's reports where the list says
nothing. **Always show** and **Omit** in the options override either.

## Entities

Controls show the requested state at once and settle on the car's next report. If
the car refuses a command, the action fails with the car's reason.

### Controls

| Entity | Type | Description |
|---|---|---|
| Climate | climate | Modes Off, On (heat/cool to target) and Fan only (blower without compressor). Target 18–32 °C. Fan 1–7, or Off to stop the A/C. Airflow: Cabin, Cabin + Feet, Feet, Feet + Windscreen (car switched on). Presets Heating (Auto at 32 °C) and Cooling (Auto at 18 °C). Each start runs for the A/C run time. |
| Pre-conditioning | switch | Cabin A/C on or off. |
| Recirculation | switch | On recirculates, off draws fresh air. Changes only while the A/C runs. |
| Unrestricted charging | switch | On charges whenever plugged in. Off returns to the car's charging schedule. |
| Battery preconditioning | switch | Starts or cancels battery preconditioning. |
| Steering wheel heat | switch | On or off. |
| Ventilation | switch | Cabin ventilation on or off. |
| Flash lights | switch | Flashes the lights. |
| Driver seat ventilation | select | Off, Low, Medium, High. Choosing a level replaces the console's Smart mode. |
| Front passenger seat ventilation | select | Off, Low, Medium, High. |
| Doors | lock | Locks. Unlocks only when **Allow remote unlocking** is on. |
| Windows | cover | Open or close all windows. |
| Sunroof | cover | Open or close. |
| Boot | cover | Open or close. |
| Charge now | button | Charges whenever plugged in. |
| Sound horn | button | Sounds the horn. |
| Refresh | button | Reads the car's status now. |
| Turn vehicle off | button | Switches the car off. Only with **Allow remote vehicle shutdown**. |
| Fridge | switch | Fridge on or off (cars with a fridge). |
| Fridge mode | select | Off, Refrigerate, Heat, Freeze. |
| Fridge temperature | number | Refrigerate 0–20 °C, Heat 35–50 °C, Freeze −15 to −1 °C. |
| Location | device_tracker | Home/away and map position. Optional. |

### Sensors

| Entity | Description |
|---|---|
| Battery | State of charge, %. |
| Range | Remaining range, km. |
| Odometer | km. |
| Charging schedule | Unrestricted or Scheduled; the reported window and days as attributes. |
| Reported charge window start / stop | Window times as the car reports them. |
| Charge power, Charge current, Charge voltage | While charging. |
| Estimated charge time | Minutes of charging remaining. |
| Cabin temperature | °C. |
| Outside temperature | °C. |
| Cabin PM2.5 | µg/m³. |
| 12V battery | Auxiliary battery voltage. |
| Cabin fan strength | Blower speed as reported (0–10). |
| Tyre 1–4 pressure, Tyre 1–4 temperature | kPa and °C, where the car reports them. |
| GPS coordinates | Latitude and longitude. Optional. |
| Fridge after leaving | Timed or Unlimited. |
| Fridge time left | Minutes left on a timed setting. |
| Last report | When the car last reported. |
| Data freshness | Recent report, Old report, Report time unknown or Report clock ahead. |
| Last cloud refresh | When Home Assistant last read the status. |
| Polling status | Polling normally, Quiet hours, Waiting for request limit, Refresh failed or Sign in again. |
| Last command | Outcome of the latest command; failure reason and result code as attributes. |

### Binary sensors

| Entity | On means |
|---|---|
| Vehicle power | Car switched on. |
| Online | Car connected to the service. |
| Plugged in | Charging cable connected. |
| Charging | Charging. |
| Charger lock | Charging connector unlocked. |
| Lock | Unlocked. |
| Door | A door open. |
| Window | A window open. |
| Boot | Boot open. |
| A/C auto mode | A/C in Auto. |
| A/C compressor | Compressor selected (off in Fan only). |
| Anti-theft enabled | Alarm armed. |
| Driver / Front passenger / Rear left / Rear right seat ventilation state | Seat fan running; level as an attribute. |

## Options

**Settings → Devices & services → GAC Connect → Configure.**

**Vehicle settings**

| Setting | Default | Description |
|---|---|---|
| Always show | none | Show entities even if the car does not appear to have them. |
| Allow unencrypted push connections | off | Receive command results where GAC's push broker is unencrypted. Without it, results arrive at the next poll. |
| Poll interval | 300 s | Minimum 60 s. Lengthened automatically when several vehicles share the request budget. |
| Enable location tracking when parked | off | Show the location while the car is switched off. |
| Enable location tracking when moving | off | Show the location while the car is switched on. |
| Fridge / warmer box controls | on | Turn off on a trim that reports a fridge it does not have. |
| Allow remote unlocking | off | Lets the Doors lock unlock the car. |
| Allow remote vehicle shutdown | off | Shows Turn vehicle off. |
| Omit unsupported entities | none | Hide entities even if the car has them. |
| Car picture on the map | AION V picture, or initials | Image URL or `/local/…` path. |
| Quiet hours start / end | none | No polling between these times (HH:MM). |
| A/C run time | 30 min | 5–60 minutes per A/C start. |

**Detected equipment** lists which entities are shown and why, starting with your
Always show and Omit choices.

**Refresh vehicle capabilities** reads the car's capability list again, for example
after a software update.

**Charging schedule** shows the reported schedule and can set a charging window,
charge now or refresh.

## Actions

| Action | Fields | Description |
|---|---|---|
| `gac_connect.charge_now` | `vin`, `until_soc` | Charge whenever plugged in. |
| `gac_connect.set_charge_window` | `vin`, `start`, `stop`, `weekly`, `until_soc` | Charge only between `start` and `stop` (HH:MM; a stop before the start runs overnight). `weekly`: Mon=1, Tue=2 … Sun=64; 127 every day; 0 or omitted once. |
| `gac_connect.charge_pause` | `vin` | Unavailable; use `set_charge_window`. |
| `gac_connect.send_command` | `vin`, `command`, `params` | Send a command by name, for example `horn-on` or `aircon-manual-on`, with optional parameters. Unlock and shutdown need their options; PIN commands are refused. |
| `gac_connect.get_control_capabilities` | `vin` | Return the car's capability list (numeric fields only). |

`vin` is needed only with more than one vehicle. `until_soc` stops at the car's
own target charge level instead of the stop time; some cars refuse it.

## Events

`gac_connect_command_result` fires for each command result:

| Field | Description |
|---|---|
| `device_id` | Vehicle device. |
| `ok` | `true` applied, `false` refused. |
| `code` | Result code. |
| `reason` | Meaning of a refusal code. |
| `event` | Command type, for example `control_air_condition`. |
| `session_id` | Identifies the command. |

## Examples

- [`docs/example-dashboard.yaml`](docs/example-dashboard.yaml): a car dashboard
  using built-in cards. Replace the `aion_v` entity prefix with yours and paste it
  into a new dashboard's raw configuration editor.
- [`docs/example-automations.yaml`](docs/example-automations.yaml): alerts for low
  tyre pressure, low 12 V battery, car left unlocked, and doors, windows or boot
  left open.
- [`docs/example-trips.yaml`](docs/example-trips.yaml): weekly, monthly and yearly
  distance and estimated consumption from odometer and battery readings. The
  service reports no trip history.

![Example car view](docs/images/aion-v.png)

The car renders in `docs/images/` are CC BY 4.0 (see
[`docs/images/ATTRIBUTION.md`](docs/images/ATTRIBUTION.md)).

## Request limits

All GAC Connect entries share one request budget:

- one request at a time, at least 1 s apart;
- at most 20 a minute, 240 an hour and 3000 a day;
- commands: at most 6 a minute and 60 an hour;
- after a "too many requests" answer, nothing is sent for at least a minute (up to
  a day if the service asks).

A held-back request is not sent: entities keep their last reading and a command
fails with an error. The budget survives restarts. If its saved state is
unreadable, requests pause for a day; delete `.storage/gac_connect.request_limits`
and restart to reset it.

## Limitations

- Sign-in needs a person: a slide puzzle and an SMS code.
- If the car's session ends, it is renewed from the saved account session; if that
  fails, Home Assistant asks you to sign in again.
- Engine start and stop and charger release need the car's remote-control PIN and
  are not supported.

## Acknowledgements

This project stands on the shoulders of the community projects that brought other
EV brands into Home Assistant and Python, among them:

- [AwangYes/BYD-re](https://github.com/AwangYes/BYD-re) — BYD
- [Hyundai-Kia-Connect/kia_uvo](https://github.com/Hyundai-Kia-Connect/kia_uvo) — Hyundai / Kia
- [bimmerconnected/bimmer_connected](https://github.com/bimmerconnected/bimmer_connected) — BMW / Mini
- [SAIC-iSmart-API/saic-python-client-ng](https://github.com/SAIC-iSmart-API/saic-python-client-ng) — MG / SAIC
- [kvanbiesen/bmw-cardata-ha](https://github.com/kvanbiesen/bmw-cardata-ha) — BMW CarData

Thanks to their authors for showing what a good community integration looks like.

## Changes

- **0.4.0b2** — fixes **UK (`GB`) sign-in**, which failed at the very first step
  with a signature error and never showed the slide puzzle. The integration was
  loading GAC International's key material for every region instead of the one the
  region's own app uses, so UK requests were signed with the wrong key and the
  backend rejected them. `GB` now signs with the My AION material. No change for
  other regions. Still requires `gac-connect` 0.4.0b1 and Home Assistant 2025.2+.

- **0.4.0b1** — requires `gac-connect` 0.4.0b1, which adds support for markets that
  run a separate national app. **United Kingdom (`GB`)** now works, using the
  My AION app and its own backend; select `GB` when adding the integration. Israel
  (`IL`) is added to the region list too. UK sign-in is still being confirmed by a
  UK user, so treat `GB` as best-effort for now. Requires Home Assistant 2025.2+.

- **0.3.0b1** — requires `gac-connect` 0.3.0b1, which stops the official app and
  this integration from signing each other out. After you sign in, a later app
  sign-in no longer ends the session; signing in here still signs the app out
  once, and you sign the app back in. If the integration was signing out whenever
  you used the phone app, **re-authenticate once** to pick up the fix. The library
  also now supports every region the service supports. Requires Home Assistant 2025.2 or newer.

- **0.2.0b12** — community contributions from @panosru: Greece (`GR`) region;
  an option to hide the fridge controls (for trims that report a fridge but have
  none); the location tracker now shows a picture of the car on the map (a
  bundled AION V render or your own image); and an example trips-and-consumption
  package (`docs/example-trips.yaml`). Requires `gac-connect` 0.2.0b10 and
  Home Assistant 2025.2 or newer.

- **0.2.0b11** — request limits: every request goes through the library's shared
  limiter (one at a time, rolling budgets, a pause after any "too many requests"
  answer); refreshes triggered by command results are at least 10 seconds apart;
  a refresh held back by the limits keeps the last reading instead of going
  unavailable; a stored poll interval under a minute is raised to one minute; the
  limits are kept across restarts.
  Signing in again after the session expires now updates the existing entry (it
  used to stop at "already set up"). Sign-in shows a clear message when requests
  are being held back. Quiet hours follow Home Assistant's time zone. The example
  dashboard's Fridge section notes which tiles send commands and shows Time left
  only with a timed setting. Requires `gac-connect` 0.2.0b9.

- **0.2.0b10** — two fridge sensors: *Fridge after leaving* (the car's
  keep-running setting, timed or unlimited) and *Fridge time left* (minutes left
  on a timed setting). Requires `gac-connect` 0.2.0b8. Added to the example
  dashboard's Fridge section.

- **0.2.0b9** — fridge / warmer box on cars that have one: switch, mode
  selector and target temperature, sharing one command queue and remembering the
  last mode and temperatures across restarts. Requires `gac-connect` 0.2.0b7. The
  example dashboard gains a Fridge section.

- **0.2.0b8** — docs only, no code change. The example dashboard now covers the
  sensors added since 0.2.0b2 (charge power and voltage, last report, charger
  lock, cabin PM2.5, tyre temperatures, refresh and battery-preconditioning
  buttons, location tile). The charging switch tile is named for what it does:
  on charges whenever the car is plugged in, off waits for the car's own
  schedule. Tyres are numbered rather than assigned corners.

- **0.2.0b7** — climate: the mode selector in the more-info dialog now works (`set_hvac_mode` was missing; heat_cool starts the A/C at the setpoint, off stops it).

## License

MIT. Includes the [`gac-connect`](https://github.com/leecaochang/gac-connect)
library.
