# GAC Connect for Home Assistant (unofficial)

> **⚠️ BETA — not fully tested, use entirely at your own risk.** This is an
> unofficial integration with no relationship to GAC. Expect bugs; things that
> worked yesterday may not work tomorrow. Remote commands physically act on your
> vehicle (A/C, locks, windows, tailgate, charging) and may misbehave or fail;
> using it alongside the official app may sign one of them out. Nothing here is
> warranted to work, keep working, or be safe. Review what an automation can do
> before you let it touch the car.

Monitor and control a **GAC / Aion** vehicle in Home Assistant — battery, range,
odometer, charging, doors and windows, tyres, and charge control.

> This project is not affiliated with, endorsed by, or supported by GAC or its
> affiliates. GAC and AION are third-party trademarks of their respective
> owners. Use it with a vehicle you own, on your own account.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=leecaochang&repository=ha-gac-connect&category=integration)

This fork ships a tested copy of its maintained GAC library with the integration.
It can release fixes independently of the original projects. Upstream remains
[wattferry/ha-gac-connect](https://github.com/wattferry/ha-gac-connect) and
[wattferry/gac-connect](https://github.com/wattferry/gac-connect); the changes here
do not imply upstream endorsement or adoption.

Supports **Australia and New Zealand**. Other regions, including Thailand, appear
in the list but are best-effort.

## Install (HACS)

Releases are published as **pre-releases** while the project is in beta. In
HACS, open the integration's page and enable *Show beta versions* to see them.

Click the badge above for one-click add, or add it manually:


1. HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/leecaochang/ha-gac-connect`, category **Integration**.
3. Install **GAC Connect (unofficial)**, then restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → GAC Connect.**

The GAC library is included in the HACS download. This works on Home Assistant OS
without copying a separate library, installing a wheel, or choosing a library in
setup. Home Assistant still installs the declared MQTT dependency when needed;
its HTTP and encryption dependencies come with Home Assistant. Installing the
integration does not make the GAC cloud service available offline.

Upgrading this integration updates its bundled library at the same time. Restart
Home Assistant after installing or changing versions. Existing accounts and
options are retained; no new sign-in is required solely because of vendoring.
Selecting an earlier integration release also selects that release's library
arrangement, which may install an external package for older releases. Make a
Home Assistant backup before downgrading across changes to stored data.

## Set-up

You sign in with the mobile number on your GAC account:

Use a local number, or prefix an international number with `+` or `00` and the
country code for the selected region. Spaces, hyphens and parentheses are accepted.
A local number beginning with `61` keeps those digits outside Australia too.

1. Choose your region and enter your mobile number.
   For Thailand, select **TH** and enter either a local number (for example,
   `0812345678`) or an international number (for example, `+66812345678`).
2. A window opens with a **slide puzzle** — drag the piece into the gap and press
   Verify (arrow keys nudge it a pixel).
3. Enter the **SMS code** sent to your phone.
4. Pick your vehicle, then choose an equipment profile. The **Aion UT** preset
   omits the fridge, steering wheel heat, sunroof and all eight tyre pressure and
   temperature sensors. This also applies to older UT entries without a stored
   profile. **Aion V** allows the fridge when the car reports one.
   **Auto detect** learns which readings are available and reveals related
   controls, without sending commands to test the equipment.
   For another model or trim, choose **Other or custom equipment** and select
   the entities your vehicle lacks. You can change profiles and choices later in the
   integration options.

If the puzzle is off by a little it just shows a new one — keep going until it
takes.

The puzzle expires after ten minutes. Canceling setup closes its connection and
invalidates its verification URL; start setup again if the page has expired.
The slider has a keyboard label and verification status is announced to assistive
technology. GAC still requires solving a visual puzzle; no nonvisual alternative
has been verified for this service.

## Entities

- **Sensors**: battery, range, odometer, cabin temperature and PM2.5, 12 V battery,
  charge power, voltage, current and estimated time, per-tyre pressure and temperature, and the
  time of the car's last report (the reported charge window is available as
  disabled-by-default diagnostics).
- **Binary sensors**: plugged-in, charging, door / window / boot open, lock,
  charger lock, online.
- **Climate**: cabin pre-conditioning (A/C, auto mode) with a target temperature;
  a run lasts the "A/C run time" option (default 30 minutes). A temperature
  selected while off is kept for the next start, including a start through the
  pre-conditioning switch. Both controls share their requested state, which
  reverts if rejected or unconfirmed after three minutes.
- **Lock**: lock the doors from Home Assistant. Unlocking does not require a PIN;
  enable **Enable door unlocking** in the options to use it. Unlocking remains
  off by default, and existing preferences are preserved. The Aion UT reports
  rear lock readings as unknown, but live locked/unlocked
  samples show its two front readings change reliably. The integration uses
  those front readings for the UT; other cars retain an unknown state if any
  door-lock reading is unknown. State changes made with the physical remote
  appear after the next status poll (every five minutes by default).
- **Covers**: windows, sunroof and tailgate (state may read unknown when the car
  does not report it; open / close still work). Opening really opens them — treat
  automations that touch these with care.
- **Switches**: pre-conditioning (plain A/C on / off), scheduled charging (the
  charge gate), steering-wheel heat, cabin ventilation, flash lights. The charging
  switch is on in unrestricted mode and off in scheduled mode; off does not mean
  charging is paused. Its **Off action is disabled**; use an explicit charge window.
- **Buttons**: charge now, sound horn, precondition battery, refresh. The existing
  **Pause charging button is disabled** and explains why when called: its old
  implementation allowed charging daily from 03:00 to 03:30. Refresh also works
  during quiet hours, subject to the request limits.
- **Fridge** (only on cars that have one): an on/off switch, a mode selector
  (off, refrigerate, heat, freeze) and a target temperature whose range follows
  the mode (refrigerate 0 to 20 °C, heat 35 to 50 °C, freeze −15 to −1 °C).
  Switching on resumes the last mode at its last temperature; these are kept
  across restarts. Changing the temperature while the fridge runs sends it
  straight away; while it is off the value is kept for the next start. A request
  shows at once and reverts if the car refuses it or has not confirmed it within
  three minutes. How long it keeps running after you leave the car is a setting
  in the car: *Fridge after leaving* shows it as timed or unlimited, and with a
  timed setting *Fridge time left* counts down in minutes once you leave. An
  automation can watch the switch and attempt a restart. The entities appear once
  the car first reports a fridge. If your car has no fridge but they show up
  anyway, turn off *Fridge / warmer box controls* in the options to remove them.
- **Location tracker** (off by default — enable it in the integration's options).
  On the map it shows a picture of the car: a built-in render for the AION V, or
  your own image set in the options (other models show the entity's initials
  until you set one).

## Example dashboard

![Example car view](docs/images/aion-v.png)

The car renders in `docs/images/` are CC BY 4.0 (see `docs/images/ATTRIBUTION.md`);
copy them to `config/www/car/` to use them in the example.

A ready-made car view using only built-in cards (no extra installs) is in
[`docs/example-dashboard.yaml`](docs/example-dashboard.yaml). Replace the `aion_v`
entity prefix with your vehicle's, then paste the whole file into a new
dashboard via Dashboards → Add Dashboard → Edit → Raw configuration editor.

It covers battery, range, odometer and report freshness; charging state with
power, current and voltage while charging; the remote controls; locks, openings,
cabin temperature and air quality; tyre pressures and temperatures; location;
and, on cars that have one, the fridge (on/off, mode, temperature and the
keep-running setting). The file's header comments explain how to switch tyre pressure to psi,
enable location tracking, and remove the cards that send commands to the car.

## Trips and consumption

Unfortunately GAC's API does not provide trip figures: it reports the odometer
and the battery level, but no trip history, no weekly, monthly or yearly
distance, and none of the car's trip computer values.
[`docs/example-trips.yaml`](docs/example-trips.yaml) is a package that works
them out in Home Assistant instead, with its own utility meters and template
sensors: kilometres driven this week, this month and this year, counted by Home
Assistant from the odometer readings, and the average consumption over each
(kWh/100 km). The example dashboard's Trips & consumption section shows them.

Consumption is estimated from the drop in battery level between readings, times a
pack size you set in the file — an estimate from the percentages Home Assistant
observes, not a measured figure. Charging is not counted; drain while parked is;
and a drive-plus-recharge between two readings is missed, so a period can read
high or low. Totals start when you add the package; the file explains how to
start the overall average from your car's trip computer instead.

**Note:** the average consumption is Home Assistant's estimate, not the car's
figure. The API has an average-consumption field, but it does not match the
trip computer (it reads 0.0 on some cars), so the package does not use it.

## Command results

Remote commands are applied asynchronously. When the push feed is connected, the
integration receives command results and requests a status refresh (subject to
the request limits). Otherwise confirmation relies on polling. A refused command
clears the requested state and is logged. Each result also fires a
`gac_connect_command_result` event (`device_id`, `ok`, `code`, `event`,
`session_id`) for automations.

Push connections require TLS by default. If a regional backend supplies only a
plaintext broker, push stays disconnected and polling continues. The **Allow
unencrypted push connections** option explicitly permits that broker; its
credentials and traffic can be intercepted and results forged by a network
attacker. Enveloped messages are required, but the protocol's encryption alone
does not authenticate the sender. Leave this option off on untrusted networks.

## Services

`gac_connect.charge_now`, `set_charge_window` (a daily
start/stop window, optionally on selected days) and `send_command` (advanced, for
the non-PIN commands by name). Climate and lock are ordinary `climate.*` /
`lock.*` actions on their entities.

`charge_pause` remains registered for existing automations, but returns an error
without sending a command. A continuous pause has not been verified. Charge
windows require distinct `HH:MM` start and stop times and a day mask from 0 to
127; overnight windows run into the following day. Times use the client library's
configured regional time zone (for Australia, Australia/Brisbane).

Unknown charger and opening readings stay unknown. An opening group is shown as
closed only when all readings are known closed. Tyres 1–4 follow the API's tyre IDs,
including sparse or reordered reports; their physical corners remain unverified.

Tyre values require the vehicle's cloud service to provide actual measurements.
The [Thailand AION UT specification](https://resource-www-sg.aionauto.com/static/TH/tenant/cms/common/202506/AION_UT_SPEC_B.pdf)
lists indirect tyre monitoring. A live Thailand UT report supplies `-1` for both
pressure and temperature on all four tyres, even with both reading flags set to
`1`; those paired placeholders display as unknown. A temperature of −1 °C with a
valid pressure remains a real reading. Indirect monitoring can warn of pressure
loss without reporting measured pressures or temperatures. The UT preset removes
all eight tyre entities. Other UT markets can have different tyre-monitoring
equipment: select Auto detect to reveal individual readings if they are reported,
or choose a custom profile and omit the ones your car does not provide.

Example alerts — low tyre pressure, low 12 V battery, left unlocked at home,
door / window / boot left open — are in
[`docs/example-automations.yaml`](docs/example-automations.yaml).

## Options

Poll interval, quiet hours (skip polling overnight),
whether the location tracker is enabled, whether the fridge controls are shown,
which other sensors and controls to omit, the car's picture on the map (an image
URL or a `/local/` path), and how long an A/C run lasts. The **Vehicle profile**
selector also lets existing installations switch to **Auto detect**. The UT
preset removes fridge, steering wheel heat, sunroof and all eight tyre readings;
older UT entries use that preset until you explicitly select another profile.
The poll interval is at least 60 seconds (default 5 minutes).
Quiet hours use Home Assistant's configured time zone. Enter two different times
in `HH:MM` format; windows can cross midnight. Clear both fields to disable quiet
hours. Manual refreshes and command-result refreshes still run during that window.

### Auto detect

Choose **Auto detect** during setup, or open **Settings → Devices & services →
GAC Connect → Configure** and change **Vehicle profile**. Switching away from
the UT preset clears its preset omissions and allows fridge detection; unrelated
manual omissions, location privacy and the unlock setting are preserved.

- Discovery uses the usual status polls. Valid zero/off/closed readings count;
  missing values, invalid readings and known placeholders do not. Tyre pressure
  and temperature are assessed separately for each tyre ID. Newly discovered
  entities appear without a restart.
- Known model exclusions supply defaults, but positive readings can reveal a
  variant's equipment in Auto detect. The UT preset itself keeps its exclusions.
- Unknown features stay hidden. **Always show (Auto detect only)** reveals a
  sensor or control you know exists, even before useful readings arrive.
  **Omit unsupported entities** hides a mistaken detection. The separate fridge,
  location and unlock options remain in force. Continuous charging pause remains
  unsupported and cannot be enabled by an override.
- Learned evidence is saved for that vehicle and survives restarts and empty
  reports. Existing entities keep their IDs; user-disabled registry entries stay
  disabled. Removing the integration entry removes its learned evidence.
- Diagnostics list each feature's evidence, manual overrides and individual
  command results. A sunroof's reported state can reveal the cover, but does
  **not** confirm remote opening or closing. Each action is confirmed only by a
  matching successful result from a command you requested. HTTP acceptance and
  generic failures do not establish support or absence. Commands without a
  matching result remain unconfirmed, including when push is unavailable.

This is best-effort detection, not a factory equipment inventory: no verified
equipment-list endpoint is currently used. Some vehicles may report plausible
dummy values; some supported features may never provide readings. Controls such
as the horn or ventilation may need an Always show override, or a successful
explicit service action, before they appear. Detection never tries commands on
its own and cannot guarantee that a remote action works on every trim.

## Request limits

The integration is built to go easy on GAC's service. Status is polled on the
interval above (never more often than once a minute); refresh presses and the
refreshes that follow commands are merged, and refreshes triggered by command
results are at least 10 seconds apart. Underneath, every request goes through the
`gac-connect` library's limiter, shared by all GAC Connect entries in Home
Assistant: one request at a time, at most one a second, 20 a minute, 240 an hour
and 3000 a day, with commands to the car limited to 6 a minute and 60 an hour. If
the service answers "too many requests", nothing is sent for at least a minute
(longer if the service says so, up to a day).
A request held back by these limits is not sent at all: the entities keep their
last reading, and a command shows an error. The limiter's state is kept in Home
Assistant's storage, so a restart does not reset it or cut short a pause the
service asked for. (If that saved state is ever unreadable, requests pause for a
day, since any pause recorded in it is unknown; deleting
`.storage/gac_connect.request_limits` and restarting resets it.)

## Notes

- Diagnostics omit picture URLs and unreviewed options, and redact known personal
  identifiers from entry data and telemetry. Review an export before sharing it:
  an upstream service can add fields the integration has not encountered before.

- Temporary server failures during token refresh preserve the saved session.
  An expired or explicitly rejected refresh token still requires reauthentication.
- Sign-in reports connection failures as retryable form errors. If the optional
  account-token rotation fails after vehicle sign-in, that sign-in remains usable;
  a warning notes that a later official-app sign-in may still end the session.
- Credentials use a private, atomically written per-entry file in Home Assistant's
  `.storage` directory. Existing config-entry credentials migrate automatically
  after a successful write; removing the integration removes its credential file.

- Sign-in needs a human (a slide puzzle and an SMS code); there is no headless login.
- Door unlock does not require a PIN. The bundled library includes the corrected
  public unlock method; enable unlocking in the integration options to use it.
  Remote power and charger-release remain blocked; no PIN is stored.
- Using the official app and this integration on the same account at the same time
  can occasionally sign one of them out.

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

- **Unreleased** — fixes unknown sensor states, tyre identification, malformed
  telemetry, unreported UT tyre-temperature placeholders, overnight charge
  windows and input validation. Cabin A/C controls
  share their setpoint and command queue, honor combined mode/temperature actions,
  and expire unconfirmed requests independently of polling. Manual refresh works
  during quiet hours. Continuous charging pause and the charging switch's Off
  action are disabled until a true stop command is verified.
  The UT preset removes all eight tyre readings. A new Auto detect profile learns
  equipment from reports and confirmed user actions, with manual overrides and
  persistent evidence. This fork now bundles its maintained library snapshot,
  removes the external `gac-connect` requirement and b1 compatibility copies, and
  reports the exact library source revision in diagnostics.

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

## Development

The offline regression suite uses mocked vehicle responses and sends no vehicle
commands. With Python 3.14, install `requirements-dev.txt`, then run
`python -m pytest` and `ruff check custom_components tests tools`. The suite is
also checked against Home Assistant 2025.2.0 with Python 3.13 and 2026.10.0 with
Python 3.14. Node 22 is needed for the embedded CAPTCHA script's race and rendering
tests.

The validation workflow runs tests and static checks on those three HA versions
with the exact manifest requirements and bundled source. It checks the recorded
file hashes and exercises a standalone copy of the integration without an
external GAC library, and with an incompatible external GAC package present.
Both regional material bundles are tested after relocation. This is an offline
compatibility matrix, not evidence of successful vehicle commands or of support
for every intervening/future HA release. Repository administrators must make these
jobs required checks in their branch protection settings; adding the workflow
alone does not enforce that.

### Where the library comes from

The canonical library source is [this fork's separate library repository](https://github.com/leecaochang/gac-connect).
An unchanged snapshot of its runtime package lives under
`custom_components/gac_connect/_vendor/gac_connect/`. All integration imports use
that private namespace. The sibling checkout and any globally installed
`gac_connect` package are not used at runtime; Python's global import paths and
module names are not patched. No library source selector is required.

The snapshot includes the library license, typing marker and regional material
files. The standalone CLI is excluded, so its optional Typer and Rich packages
are not installed. This is **not a zero-dependency integration**: the library
still uses `aiohttp`, `cryptography` and `aiomqtt`. Home Assistant supplies the first
two; `manifest.json` pins the additional MQTT requirement. Tests verify that the
installed versions meet the snapshot's recorded requirements.

`_vendor/provenance.json` records the source repository, full Git commit, library
version, dependencies, exclusions and file hashes. Diagnostics include the source,
version and commit too. The version label alone is insufficient: this fork's
patched `0.4.0b5` snapshot is identified by its commit and is not the PyPI release
with the same version label.

### Updating the bundled library

1. Make and test behavior changes in the **library repository**, then commit them
   there. Do not edit the generated copy in the integration.
2. From the integration repository, bundle the committed library revision:

   ```sh
   python tools/vendor_library.py --source ../gac-connect
   python tools/vendor_library.py --check --source ../gac-connect
   python -m pytest
   ruff check custom_components tests tools
   ```

   Use `--ref COMMIT` to choose a specific revision. Updates read Git objects and
   therefore exclude uncommitted changes. Verification with `--source` compares
   the recorded revision even if the library branch has advanced; add `--ref HEAD`
   to compare the bundle with its current tip. The command refuses to overwrite
   modified or unexpected files in the bundle; review and preserve them first.
3. Review the generated diff and dependency requirements. Commit the snapshot
   update separately from behavior changes, then release the tested integration
   and snapshot together. Before publishing, make the recorded library commit
   available in the source repository so others can reproduce it.

`python tools/vendor_library.py --check` verifies the shipped hashes without the
source checkout, Git, or network access. Hashes detect accidental drift; comparing
with the recorded source commit also verifies provenance. CI always tests the
committed bundle and does not download an arbitrary candidate library.

Capability detection, clients, telemetry decoding and command behavior remain
library responsibilities. Home Assistant owns entities, services, configuration,
storage and correlation of command results. The old b1 backports have been
removed, so there is one runtime implementation of library behavior.

### Contributing upstream

Offer library fixes and their tests to the original library project independently
of this integration's packaging. Offer integration fixes on focused branches
based on upstream, excluding vendoring changes. Separate commits on one branch
do not automatically produce separate pull requests. Upstream can keep using a
published library while this fork vendors its tested snapshot; accepting bug
fixes does not require adopting vendoring. Maintainers of this fork must review
upstream bug and security fixes and deliberately refresh the snapshot.

## License

MIT. Includes a maintained fork of [wattferry/gac-connect](https://github.com/wattferry/gac-connect).
The bundled library's [MIT license and copyright notice](custom_components/gac_connect/_vendor/gac_connect/LICENSE)
are preserved with its source.
