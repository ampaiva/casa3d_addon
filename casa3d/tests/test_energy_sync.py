import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import energy, energy_dashboard, energy_learning


class Client:
    def __init__(self, prefs, entities, devices, states, statistics):
        self.prefs = copy.deepcopy(prefs)
        self.results = {'config/entity_registry/list': entities, 'config/device_registry/list': devices,
                        'get_states': list(states.values()), 'recorder/list_statistic_ids': statistics}
        self.saves = 0
        self.reads = 0
        self.concurrent = False

    def call(self, command, **kwargs):
        if command == 'energy/get_prefs':
            self.reads += 1
            if self.concurrent and self.reads == 2:
                self.prefs['device_consumption'][0]['name'] = 'Edicao concorrente'
            return copy.deepcopy(self.prefs)
        if command == 'energy/save_prefs':
            self.saves += 1
            self.prefs.update(copy.deepcopy(kwargs))
            return copy.deepcopy(self.prefs)
        return copy.deepcopy(self.results[command])


def appliance():
    eid = 'sensor.freezer_energy'
    device = {'id': 'freezer', 'name': 'Freezer', 'identifiers': [['tuya', 'socket']]}
    entry = {'entity_id': eid, 'device_id': 'freezer', 'device_class': 'energy'}
    state = {'entity_id': eid, 'state': '14.23', 'attributes': {
        'state_class': 'total_increasing', 'device_class': 'energy', 'unit_of_measurement': 'kWh'}}
    stat = {'statistic_id': eid, 'has_sum': True, 'statistics_unit_of_measurement': 'kWh'}
    return eid, [entry], [device], {eid: state}, [stat]


class Names(unittest.TestCase):
    def test_rename_preserves_history_references_options_and_is_idempotent(self):
        eid, entities, devices, states, stats = appliance()
        prefs = {'energy_sources': [{'type': 'solar', 'stat_energy_from': 'sensor.solar'}],
                 'device_consumption': [{'stat_consumption': eid, 'name': '16A Estimate Socket 5',
                                         'stat_rate': 'sensor.freezer_power', 'included_in_stat': 'sensor.parent'}],
                 'device_consumption_water': [{'stat_consumption': 'sensor.water'}]}
        client = Client(prefs, entities, devices, states, stats)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'energy-dashboard-sync.json').write_text(json.dumps({
                'managed': {eid: energy_dashboard.identity(devices[0])}}))
            result = energy_dashboard.sync(client, directory)
            expected = copy.deepcopy(prefs)
            expected['device_consumption'][0]['name'] = 'Freezer'
            self.assertEqual(client.prefs, expected)
            self.assertEqual(len(result['renamed']), 1)
            backups = list(Path(directory).glob('energy-prefs-before-*.json'))
            self.assertEqual(json.loads(backups[0].read_text()), prefs)
            energy_dashboard.sync(client, directory)
            self.assertEqual(client.saves, 1)
            devices[0]['name_by_user'] = 'Freezer da Cozinha'
            energy_dashboard.sync(client, directory)
            self.assertEqual(client.prefs['device_consumption'][0]['name'], 'Freezer da Cozinha')

    def test_concurrent_edit_defers_rename(self):
        eid, entities, devices, states, stats = appliance()
        client = Client({'device_consumption': [{'stat_consumption': eid, 'name': 'Old'}]},
                        entities, devices, states, stats)
        client.concurrent = True
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'energy-dashboard-sync.json').write_text(json.dumps({
                'managed': {eid: energy_dashboard.identity(devices[0])}}))
            result = energy_dashboard.sync(client, directory)
        self.assertEqual(client.saves, 0)
        self.assertIn('Configuracao mudou', result['status'])

    def test_multiple_channels_and_grid_sources_keep_names(self):
        eid, entities, devices, states, stats = appliance()
        entities.append({**entities[0], 'entity_id': 'sensor.other_channel'})
        prefs = {'device_consumption': [{'stat_consumption': eid, 'name': 'Canal 1'},
                                        {'stat_consumption': 'sensor.other_channel', 'name': 'Canal 2'}]}
        self.assertEqual(energy_dashboard.plan(prefs, entities, devices, states, stats, managed_ids=[eid])['renames'], {})
        prefs = {'energy_sources': [{'stat_energy_from': 'sensor.other_channel'}],
                 'device_consumption': [{'stat_consumption': eid, 'name': 'Existing'}]}
        self.assertEqual(energy_dashboard.plan(prefs, entities, devices, states, stats, managed_ids=[eid])['renames'], {})

    def test_manual_custom_name_is_preserved_but_unnamed_row_follows_device(self):
        eid, entities, devices, states, stats = appliance()
        devices[0]['name'] = '16A Socket'
        prefs = {'device_consumption': [{'stat_consumption': eid, 'name': 'Freezer da Cozinha'}]}
        self.assertEqual(energy_dashboard.plan(prefs, entities, devices, states, stats)['renames'], {})
        del prefs['device_consumption'][0]['name']
        self.assertEqual(energy_dashboard.plan(prefs, entities, devices, states, stats)['renames'], {eid: '16A Socket'})
        client = Client(prefs, entities, devices, states, stats)
        with tempfile.TemporaryDirectory() as directory:
            energy_dashboard.sync(client, directory)
            devices[0]['name'] = 'Nome atualizado'
            energy_dashboard.sync(client, directory)
            self.assertEqual(client.prefs['device_consumption'][0]['name'], 'Nome atualizado')

    def test_removed_managed_device_stays_removed(self):
        eid, entities, devices, states, stats = appliance()
        client = Client({'device_consumption': []}, entities, devices, states, stats)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'energy-dashboard-sync.json').write_text(json.dumps({
                'managed': {eid: energy_dashboard.identity(devices[0])}}))
            result = energy_dashboard.sync(client, directory)
        self.assertEqual(client.saves, 0)
        self.assertTrue(result['ignored_devices'])


def compressor():
    load, meter = [(0, 0)], [(0, 20)]
    for t in range(300, 3900, 600):
        load.extend([(t, 500), (t + 1, 100), (t + 200, 60), (t + 201, 0)])
        meter.extend([(t + 5, 120), (t + 205, 20)])
    load.append((4200, 0))
    meter.append((4200, 20))
    return load, meter


class Detection(unittest.TestCase):
    def test_inrush_and_two_step_shutdown_match_stable_100w_load(self):
        load, meter = compressor()
        result = energy_learning.compare(load, {'correct': meter, 'other': [(0, 50), (4200, 50)]})
        self.assertEqual(result['candidate']['entity'], 'correct')
        self.assertEqual(result['candidate']['rises'], 6)
        self.assertEqual(result['candidate']['falls'], 6)
        self.assertEqual(result['candidate']['score'], 1)

    def test_ambiguous_or_missing_reference_does_not_assign(self):
        load, meter = compressor()
        self.assertEqual(energy_learning.compare(load, {'one': meter, 'two': meter})['reason'], 'ambiguous')
        result = energy_learning.compare(load, {'one': meter}, missing_channels=['phase_B'])
        self.assertIsNone(result['candidate'])
        self.assertEqual(result['reason'], 'missing_channels')

    def test_spikes_and_outages_do_not_become_evidence(self):
        load = [(0, 0)]
        for t in range(300, 3900, 600):
            load.extend([(t, 500), (t + 1, 0)])
        load.append((4200, 0))
        self.assertEqual(energy_learning.compare(load, {'meter': load})['events'], 0)
        load, meter = compressor()
        for t in range(300, 3900, 600):
            load.extend([(t + 10, None), (t + 20, 100), (t + 210, None), (t + 220, 0)])
        self.assertEqual(energy_learning.compare(sorted(load), {'meter': meter})['events'], 0)

    def test_detection_is_displayed_without_overwriting_explicit_wiring(self):
        eid, entities, devices, states, stats = appliance()
        controls = {'device_positions': {'terreo': {eid: {'ha_device_id': 'freezer', 'title': 'Freezer'}}}}
        registry = {'devices': devices, 'entities': entities, 'areas': []}
        evidence = {'devices': {'freezer': {'candidate': {'score': .9}, 'meter': {
            'entity': 'sensor.phase_c', 'title': 'Medidor', 'panel_label': 'Quadro esquerdo', 'channel': 'C'}}}}
        result = energy.discover(controls, registry, lambda x: x, evidence)[0]
        self.assertEqual(result['detected_supply']['phase'], 'C')
        self.assertIn('Quadro esquerdo', result['assignment_status'])
        self.assertEqual(result['assignment'], {})
        controls['infrastructure'] = {'panels': {'right': {'circuits': []}},
                                      'feeds': {eid: {'panel': 'right', 'phase': 'A'}}}
        result = energy.discover(controls, registry, lambda x: x, evidence)[0]
        self.assertIsNone(result['detected_supply'])
        self.assertEqual(result['assignment']['phase'], 'A')


if __name__ == '__main__':
    unittest.main()
