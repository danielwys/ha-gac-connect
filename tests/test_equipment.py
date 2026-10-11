"""Explain observations, absence of evidence and overrides without probing."""
import asyncio
from types import SimpleNamespace

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.config_flow import GacOptionsFlow
from custom_components.gac_connect.equipment import equipment_summary
from test_autodetect import vehicle


def test_options_menu_and_read_only_equipment_explanations(tmp_path):
    async def run():
        async with vehicle(tmp_path, model='Aion UT', options={
            'enable_entities': ['button:horn'], 'omit_entities': ['sensor:pm25'], 'enable_fridge': False,
        }) as (hass, entry, c):
            c.capabilities.observe(decode_status({'steeringAccessors': [{'steering': 0}]}))
            c.capabilities.confirm_command('sunroof-close')
            before = dict(entry.options)
            flow = GacOptionsFlow()
            flow.hass, flow.handler = hass, entry.entry_id
            menu = await flow.async_step_init()
            assert menu['type'] == 'menu' and 'equipment' in menu['menu_options']
            page = await flow.async_step_equipment()
            text = page['description_placeholders']['equipment']
            assert 'usable vehicle reading was observed' in text
            assert 'Successful command results: sunroof-close.' in text
            assert 'Successful command results: sunroof-open' not in text
            assert 'no usable evidence yet' in text
            assert 'Hidden by the model default' in text
            assert 'Hidden by your Omit choice' in text
            assert 'Shown by your Always show choice' in text
            assert 'fridge controls are turned off' in text
            assert 'location tracking is turned off' in text
            assert 'VIN' not in text
            assert (await flow.async_step_equipment({}))['type'] == 'menu'
            assert dict(entry.options) == before
            c.client.command.assert_not_called()
            c.client.get_status.assert_not_called()
    asyncio.run(run())


def test_unloaded_entry_explains_profile_without_claiming_detection():
    entry = SimpleNamespace(data={'model': 'Aion UT'}, options={})
    text = equipment_summary(entry)
    assert 'Auto detect evidence is available only while that profile is loaded' in text
    assert 'Omitted by the selected model profile' in text
    assert '**Switch · Fridge** — Omitted by the selected model profile.' in text
    assert 'a usable vehicle reading was observed' not in text
