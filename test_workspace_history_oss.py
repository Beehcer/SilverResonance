import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from workspace_history import WorkspaceHistoryArchive
from workspace_history_oss import HistoryPublisher, BASE, PREFIX, configure_history_publication
from scripts.publish_workspace_assets import asset_urls


class HistoryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = WorkspaceHistoryArchive(Path(self.temp.name) / 'cache')
        self.bucket = Mock()
        self.verify = Mock()
        self.clock = 1794672000
        self.publisher = HistoryPublisher(self.archive, self.bucket, clock=lambda: self.clock, verify=self.verify)
        self.raw = json.dumps({'schema': 1, 'symbol': 'CZCE.MA701', 'period': 'm3',
                              'candle_basis': 'native_v1', 'anchor_reference_id': None,
                              'bars': [{'dt': '2026-11-13T15:00:00+08:00', 'open': 3199,
                                        'high': 3201, 'low': 3198, 'close': 3200}]}).encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.block = self.archive.root / 'blocks' / (self.digest + '.json')
        self.block.parent.mkdir(parents=True)
        self.block.write_bytes(self.raw)
        self.item = {'sha256': self.digest, 'path': self.block, 'last': '2026-11-13T15:00:00+08:00'}

    def test_no_network_on_enqueue_and_url_only_after_public_verification(self):
        self.publisher.enqueue([self.item])
        self.bucket.put_object.assert_not_called()
        self.assertIsNone(self.publisher.public_url(self.digest))
        self.assertTrue(self.publisher.publish_one())
        self.verify.assert_called_once_with(self.digest)
        self.assertEqual(self.publisher.public_url(self.digest), BASE + PREFIX + self.digest + '.json')
        args = self.bucket.put_object.call_args
        self.assertEqual(args.args[0], PREFIX + self.digest + '.json')
        self.assertEqual(gzip.decompress(args.args[1]), self.raw)
        self.assertEqual(args.kwargs['headers']['x-oss-object-acl'], 'public-read')
        self.publisher.enqueue([self.item])
        self.assertFalse(self.publisher.publish_one())

    def test_invalid_path_and_corrupt_content_never_publish(self):
        self.publisher.enqueue([{**self.item, 'path': Path(self.temp.name) / 'secret.json'}])
        self.assertFalse(self.publisher.publish_one())
        self.block.write_bytes(b'corrupt')
        self.publisher.enqueue([self.item])
        self.publisher.publish_one()
        self.bucket.put_object.assert_not_called()
        self.assertIsNone(self.publisher.public_url(self.digest))

    def test_failure_preserves_origin_and_retries_without_claiming_success(self):
        self.verify.side_effect = IOError('offline')
        self.publisher.enqueue([self.item]); self.publisher.publish_one()
        self.assertIsNone(self.publisher.public_url(self.digest))
        self.assertEqual(self.archive.read_block(self.digest), self.raw)
        self.publisher.enqueue([self.item]); self.assertFalse(self.publisher.publish_one())
        self.clock += 61; self.verify.side_effect = None
        self.publisher.enqueue([self.item]); self.publisher.publish_one()
        self.assertIsNotNone(self.publisher.public_url(self.digest))

    def test_published_versions_survive_process_restart(self):
        self.publisher.enqueue([self.item]); self.publisher.publish_one()
        restored = HistoryPublisher(self.archive, self.bucket, verify=self.verify)
        self.assertEqual(restored.public_url(self.digest), self.publisher.public_url(self.digest))

    def test_gc_only_deletes_registered_expired_history_prefix(self):
        self.publisher.enqueue([self.item]); self.publisher.publish_one()
        self.archive.collect_garbage = Mock()
        self.block.unlink()
        self.publisher.collect_garbage()
        self.bucket.delete_object.assert_not_called()
        self.clock += 86401
        self.publisher.collect_garbage()
        self.bucket.delete_object.assert_called_once_with(PREFIX + self.digest + '.json')
        self.assertIsNone(self.publisher.public_url(self.digest))

    def test_nested_private_fields_never_enter_public_objects(self):
        value = json.loads(self.raw)
        value['bars'][0]['note'] = {'plan': 'private', 'credentials': 'secret'}
        raw = json.dumps(value).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.archive.root / 'blocks' / (digest + '.json')
        path.write_bytes(raw)
        self.publisher.enqueue([{'sha256': digest, 'path': path, 'last': self.item['last']}])
        with self.assertLogs('silver.history', level='WARNING') as captured:
            self.publisher.publish_one()
        self.bucket.put_object.assert_not_called()
        self.assertIsNone(self.publisher.public_url(digest))
        self.assertNotIn('secret', '\n'.join(captured.output))

    def test_gc_preserves_active_remote_when_local_copy_is_missing(self):
        self.publisher.enqueue([self.item]); self.publisher.publish_one()
        self.archive.collect_garbage = Mock()
        self.archive.live_hashes = Mock(return_value={self.digest})
        self.block.unlink()
        self.clock += 86401
        self.publisher.collect_garbage()
        self.bucket.delete_object.assert_not_called()
        self.assertIsNotNone(self.publisher.public_url(self.digest))

    def test_malformed_enqueue_metadata_cannot_redirect_publication(self):
        self.publisher.enqueue([None, {}, {'sha256': []}, {**self.item, 'last': 'secret'}])
        self.assertFalse(self.publisher.publish_one())
        self.bucket.put_object.assert_not_called()

    def test_absent_or_invalid_config_keeps_origin_cache(self):
        path = Path(self.temp.name) / 'config.json'
        self.assertIsNone(configure_history_publication(self.archive, path))
        path.write_text(json.dumps({'bucket': 'wrong', 'endpoint': 'https://example.invalid'}))
        self.assertIsNone(configure_history_publication(self.archive, path))

    def test_one_asset_change_does_not_invalidate_unchanged_asset(self):
        hashes = {'one.js': 'a' * 64, 'two.css': 'b' * 64}
        old = {'sha256': hashes.copy(), 'assets': {'/static/' + n: BASE + 'workspace/' + 'c' * 24 + '/' + n for n in hashes}}
        first = asset_urls(hashes, old)
        self.assertEqual(first['one.js'], old['assets']['/static/one.js'])
        second = asset_urls({**hashes, 'two.css': 'd' * 64}, old)
        self.assertEqual(second['one.js'], first['one.js'])
        self.assertNotEqual(second['two.css'], first['two.css'])
        old['assets']['/static/one.js'] = 'https://other.invalid/one.js'
        self.assertTrue(asset_urls(hashes, old)['one.js'].startswith(BASE))


if __name__ == '__main__':
    unittest.main()
