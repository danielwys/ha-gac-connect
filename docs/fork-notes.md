# Fork changes and upgrade guide

The fork's `main` branch builds on upstream [PR #11](https://github.com/wattferry/ha-gac-connect/pull/11)
at `d82b155`, using the published `gac-connect==0.5.0` library. The
[README](../README.md#install) covers installation through Marketplace, HACS and a manual copy.

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

Home Assistant manages Recorder retention and deletion. Removing a sensor, device or
integration entry leaves its existing statistics in Recorder, subject to its retention settings.

The custom `picture` option accepts local Home Assistant image paths and HTTP(S) URLs.
It carries forward through migration and unrelated option updates. The location tracker
and generated Vehicle card use the picture. For a dashboard you've edited, set the image
in the card editor. The card fits the entire image to its width at the original aspect ratio.
Dashboard cards and location tracking are separate options.

## Aion V lock readings

For the Aion V, the integration can infer central locking when both front doors
report locked, both rear lock codes are `-1`, all four doors report closed and the
body alarm reports armed. An explicit unlocked reading still takes precedence.
The Vehicle card and Home Assistant lock entities use the same interpretation. The
Doors entity shows **Open** when the boot reports open and **Unknown** when the
lock data is insufficient.

## Dashboard appearance

The cards use Home Assistant's surfaces, text, dividers and typography in light and dark
mode. The outer corners follow `--ha-card-border-radius` (12 px by default). Action grids
adapt to the card width and long labels wrap. Colour indicates control and vehicle status.
See the [design guide](../DESIGN.md)
and the [frontend build instructions](../custom_components/gac_connect/frontend/README.md).

## Dashboard module loading

In storage mode, the integration registers the main card bundle as a Lovelace JavaScript
module, with the same content-based cache version used by the loader. It adopts an
existing registration of this exact local module and updates it when the bundle changes.
It removes duplicates of its own local module path. The registration is shared across
opted-in cars and removed after the last car opts out. YAML resource mode uses the
page-loader fallback and leaves resource configuration to the owner.

This addresses a loading failure where stock cards render but the GAC cards show
Configuration error. Browser tests reproduced the error cards while the main script
was deliberately delayed and confirmed all cards recovered when the registered module
arrived. A phone's existing WebView still needs a refresh after registration or an update.

## Before a live upgrade

1. Take and verify a full Home Assistant backup, including configuration, `.storage`,
   the custom integration and the Recorder database. Save the installed version and commit.
2. Upgrade the existing entry in place. Do not remove and re-add the integration.
   The existing SoC entity ID, custom picture and user-disabled equipment choices are retained.
3. Review charging automations and dashboards: the upstream PR renames some controls,
   retires others and changes the charging switch to Scheduled charging.
4. Start with status readings. Check battery, range, lock state, charging, entity IDs,
   picture, disabled equipment and recorded history before enabling optional controls.

The upstream migration moves credentials from the config entry into a private atomic
`.storage/gac_connect.session.<entry_id>` file and removes the legacy field. A file-only
downgrade is not a reliable rollback. Restore the full backup if necessary; fresh GAC
authentication may still be needed because the server rotates refresh tokens.

## Validation

The [Validate workflow](https://github.com/danielwys/ha-gac-connect/actions/workflows/validate.yml)
runs integration tests on Home Assistant 2025.3.0 and 2026.10.2, checks the generated
frontend assets, and validates HACS and Home Assistant metadata.

The lock interpretation and photo sizing were verified on a Singapore Aion V with Home
Assistant 2026.11.0. The Android Companion app was used to confirm the resource-loading
fix. Capability and telemetry support depend on the vehicle and region.
