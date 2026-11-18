"""Playback-device registration and local cache must work without affecting Actions."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from drama_direct import DevicePool, DirectError
from drama_downloader import main


class PhoenixRegistrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "device_pool.json")
        self.calls = []

    def registered(self, *, mock_origin=None, app_id='1967'):
        self.assertEqual(app_id, '8662')
        n=len(self.calls)+1
        self.calls.append(n)
        return {'device_id': str(830000+n), 'install_id': str(930000+n),
                'app_id':'8662', 'origin':'server_registered',
                'cdid':f'official-{n}', 'status':'active'}

    def test_first_playback_use_registers_default_five_and_persists(self):
        pool=DevicePool(self.path)
        with patch('drama_direct.register_device',side_effect=self.registered), \
                patch('drama_direct.generate_local_device') as fake:
            selected=pool.get('8662')
            self.assertEqual(selected['origin'],'server_registered')
            self.assertEqual(len(self.calls),5)
            fake.assert_not_called()
            for _ in range(9): pool.get('8662')
        saved=json.loads(Path(self.path).read_text(encoding='utf-8'))
        self.assertEqual(len(saved['android']),5)
        fresh=DevicePool(self.path)
        with patch('drama_direct.register_device') as registrar:
            self.assertEqual(len({fresh.get('8662')['device_id'] for _ in range(10)}),5)
            registrar.assert_not_called()

    def test_explicit_count_and_incremental_prefill(self):
        pool=DevicePool(self.path,desired=2)
        with patch('drama_direct.register_device',side_effect=self.registered):
            self.assertEqual(pool.ensure(2,app_id='8662'),2)
            self.assertEqual(pool.ensure(4,app_id='8662'),4)
            self.assertEqual(pool.ensure(4,app_id='8662'),4)
        self.assertEqual(len(self.calls),4)

    def test_action_pre_registered_single_device_is_reused(self):
        data={'android':[{'app_id':'8662','origin':'server_registered',
                          'device_id':'5656','install_id':'6767','status':'active'}]}
        Path(self.path).write_text(json.dumps(data),encoding='utf-8')
        pool=DevicePool(self.path,desired=5)
        with patch('drama_direct.register_device') as registrar, \
                patch('drama_direct.generate_local_device') as fake:
            self.assertEqual(pool.get('8662')['device_id'],'5656')
            registrar.assert_not_called()
            fake.assert_not_called()
        self.assertEqual(len(pool.status()),1)

    def test_unregistered_legacy_phoenix_is_not_used_as_registered(self):
        data={'android':[{'app_id':'8662','origin':'local_generated',
                          'device_id':'fake','install_id':'fake','status':'active'}]}
        Path(self.path).write_text(json.dumps(data),encoding='utf-8')
        pool=DevicePool(self.path,desired=2)
        with patch('drama_direct.register_device',side_effect=self.registered), \
                patch('drama_direct.generate_local_device') as fake:
            selected=pool.get('8662')
            fake.assert_not_called()
        self.assertNotEqual(selected['device_id'],'fake')
        self.assertEqual(len(self.calls),2)

    def test_registration_error_does_not_create_fake_phoenix_device(self):
        pool=DevicePool(self.path,desired=5)
        with patch('drama_direct.register_device',side_effect=DirectError('offline')), \
                patch('drama_direct.generate_local_device') as fake:
            with self.assertRaisesRegex(DirectError,'offline'):
                pool.get('8662')
            fake.assert_not_called()
        self.assertEqual(len(pool.status()),0)

    def test_concurrent_first_requests_share_one_registration_batch(self):
        pool=DevicePool(self.path,desired=3)
        with patch('drama_direct.register_device',side_effect=self.registered):
            with ThreadPoolExecutor(max_workers=8) as executor:
                selected=list(executor.map(lambda _: pool.get('8662')['device_id'],range(8)))
        self.assertEqual(len(self.calls),3)
        self.assertEqual(len(set(selected)),3)

    def test_reject_last_phoenix_registers_new_instead_of_fake(self):
        pool=DevicePool(self.path,desired=1)
        with patch('drama_direct.register_device',side_effect=self.registered), \
                patch('drama_direct.generate_local_device') as fake:
            old=pool.get('8662')['device_id']
            new=pool.replace_rejected(old,app_id='8662')
            fake.assert_not_called()
        self.assertEqual(len(self.calls),2)
        self.assertNotEqual(old,new['device_id'])
        self.assertEqual(new['origin'],'server_registered')

    def test_cli_register_defaults_to_phoenix_and_respects_count(self):
        with patch('drama_direct.register_device',side_effect=self.registered):
            self.assertEqual(main(['--pool',self.path,'--devices','3','register']),0)
            self.assertEqual(main(['--pool',self.path,'--devices','3','register']),0)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(len(DevicePool(self.path).status()),3)

    def test_invalid_count_raises(self):
        with self.assertRaises(DirectError):
            DevicePool(self.path).ensure(21,app_id='8662')


if __name__=='__main__':
    unittest.main()
