# Changes on this branch

Based on upstream [PR #11](https://github.com/wattferry/ha-gac-connect/pull/11),
commit `d82b155cecdd5eeab69f9f6343ec01122f155a4b`, with published `gac-connect==0.5.0`.
This is an experimental fork, not an upstream release or a claim of manufacturer support.

## Authorization

- Custom read services require read access to an enabled entity of the selected vehicle.
- Custom command services require control access to an enabled entity for that vehicle
  and command feature. Access to a battery sensor or another vehicle does not grant command access.
  Commands without a mapped feature require administrator access.
- Option switches, polling/quiet-hour settings, capability refresh and the entity-visibility
  service require administrator access. Ordinary users can operate their permitted car
  controls, but cannot enable remote unlocking, shutdown or other security/privacy options.
- Calls carrying an unknown user ID are rejected. Calls without a user ID remain trusted
  for Home Assistant's internal operations and administrator-managed automations, following
  Home Assistant's service convention. Administrators must control automation editing.
- Existing command capability checks and explicit remote-unlock/shutdown opt-ins still apply.

Regression tests use Home Assistant's real service dispatcher and authentication groups,
with synthetic users and mocked GAC transport. They cover read-only users, ordinary users,
admins, invalid users, scoped entity permissions and disabled entities.

## History and pictures

The integration no longer clears Recorder statistics when a sensor, device or integration
entry is removed. Home Assistant owns history retention and deletion. This does not change
Recorder's configured detailed-history retention or recover statistics already deleted.

The custom `picture` option is preserved on migration and unrelated option updates.
Local Home Assistant image paths and HTTP(S) URLs are supported; executable URL schemes
are rejected. The location tracker and generated vehicle card use the picture. Changing
the picture does not overwrite a dashboard the owner has edited. Dashboard JavaScript
and location tracking remain opt-in; setting a picture enables neither.

## Aion V lock readings

The 0.5.0 library fixes the old false-Unlocked result for partial door-lock reports.
For an Aion V reporting locks `[0, 0, -1, -1]`, the result is **Unknown**: the front
locks are reported locked, while the rear locks are unreported. This fork does not
reinterpret those missing readings as Locked. No physical car commands were used in testing.

The Vehicle card now displays **Lock status unknown** rather than omitting the lock
line when neither lock entity has a reading. **Alarm armed** and **Doors & windows
closed** are separate indicators and do not stand in for a confirmed lock report.

## Dashboard appearance

The custom cards use Home Assistant's card backgrounds, text and divider colours
in light and dark mode. Typography follows Home Assistant's body/heading fonts,
and the outer card corners follow `--ha-card-border-radius` (12 px by default).
The cards no longer load Barlow fonts. Contents, controls and semantic status
colours are retained. Action grids adapt to the available width, and long button
labels wrap instead of forcing columns beyond the card edge. The bundled
JavaScript is rebuilt from the TypeScript source.

## Before a live upgrade

1. Take and verify a full Home Assistant backup, including configuration, `.storage`,
   the custom integration and the Recorder database. Save the installed version and commit.
2. Upgrade the existing entry in place. Do not remove and re-add the integration.
   The existing SoC entity ID, custom picture and user-disabled equipment choices are retained.
3. Review charging automations and dashboards: the upstream PR renames some controls,
   retires others and changes the charging switch to Scheduled charging.
4. Start with status readings. Check battery, range, lock unknowns, charging, entity IDs,
   picture, disabled equipment and recorded history before enabling optional controls.

The upstream migration moves credentials from the config entry into a private atomic
`.storage/gac_connect.session.<entry_id>` file and removes the legacy field. A file-only
downgrade is not a reliable rollback. Restore the full backup if necessary; fresh GAC
authentication may still be needed because the server rotates refresh tokens.

The implementation is tested offline, not validated against every vehicle or region.
The source review found no evidence of malicious behavior in the reviewed revision;
it cannot prove the absence of malicious code or undisclosed vulnerabilities.

## Offline validation

- Home Assistant 2026.10.4 / Python 3.14.2: 947 tests passed.
- Home Assistant 2025.3.0 / Python 3.13.2: 947 tests passed.
- Ruff static checks and Git whitespace checks passed.
- The original authorization/history patch left the frontend unchanged; its reviewed
  base reproduced all 14 committed assets. The later colour and status-label update
  is limited to the frontend source and its rebuilt bundle.

These checks do not replace a live, read-only validation on the intended car.
