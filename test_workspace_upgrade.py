"""Regression coverage for independent boards, stable anchors and shared editing."""
import copy
import unittest
import test_workspace_api as base
from test_workspace_api import SYMBOL, drawing
from workspace_store import WorkspaceStore, board_key


class WorkspaceUpgradeTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    market = base.WorkspaceApiTests.market
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    join = base.WorkspaceApiTests.join
    save = base.WorkspaceApiTests.save
    patch_plan = base.WorkspaceApiTests.patch_plan

    def create(self, body):
        response = self.owner.post('/api/workspace/boards?symbol=' + SYMBOL, json=body, headers=self.owner_headers)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['plan']

    def test_current_editor_wins_on_same_object_and_preserves_unrelated_changes(self):
        self.save({'notes': 'original', 'objects': [drawing('a', 100), drawing('b', 200)]})
        peer, headers = self.join('Peer')
        self.patch_plan(1, changes={'notes': 'peer note'}, upserts=[drawing('a', 110), drawing('b', 220)], client=peer, headers=headers)
        response = self.owner.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
            'base_revision': 1, 'conflict_policy': 'editor_wins', 'changes': {'title': 'mine'},
            'upsert_objects': [drawing('a', 105)]}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['plan']['notes'], 'peer note')
        self.assertEqual([o['points'][0]['price'] for o in response.json['plan']['objects']], [105, 220])
        removed = peer.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
            'base_revision': 1, 'conflict_policy': 'editor_wins', 'delete_object_ids': ['a']}, headers=headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual([o['id'] for o in removed.json['plan']['objects']], ['b'])

    def test_editor_priority_accepts_expired_base_but_keeps_validation_and_explicit_locks(self):
        self.save({'objects': [{**drawing('a', 100), 'locked': True}]})
        response = self.owner.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
            'base_revision': 0, 'conflict_policy': 'editor_wins', 'changes': {'notes': 'latest'}}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        for data in [{'upsert_objects': [drawing('a', 101)]}, {'changes': {'objects': []}}, {'conflict_policy': 'anything', 'changes': {'notes': 'invalid'}}]:
            bad = self.owner.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
                'base_revision': 0, 'conflict_policy': 'editor_wins', **data}, headers=self.owner_headers)
            self.assertEqual(bad.status_code, 400, bad.json)

    def test_stale_mouse_hides_before_membership_expires(self):
        from unittest.mock import patch
        user = self.owner_session['user']
        with patch('workspace_store.time.monotonic', return_value=100):
            self.store.update_presence(user, SYMBOL, 'day', cursor={'time': 1794200000000, 'price': 100})
        with patch('workspace_store.time.monotonic', return_value=107):
            peers = self.store.presence(SYMBOL)
            self.assertEqual(len(peers), 1)
            self.assertIsNone(peers[0]['cursor'])
            self.assertNotIn('seen_at', peers[0])

    def test_price_segment_survives_sync_and_rejects_diagonal_or_zero_length(self):
        segment = {'id': 'segment', 'kind': 'segment', 'period': 'm30', 'name': '价位', 'color': '#32d583',
                   'points': [{'time': 1794200000000, 'price': 100}, {'time': 1794203600000, 'price': 100}]}
        response = self.save({'objects': [segment]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)['objects'][0], segment)
        for last in [{'time': 1794203600000, 'price': 101}, {'time': 1794200000000, 'price': 100}]:
            bad = self.owner.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
                'base_revision': 1, 'conflict_policy': 'editor_wins',
                'upsert_objects': [{**segment, 'points': [segment['points'][0], last]}]}, headers=self.owner_headers)
            self.assertEqual(bad.status_code, 400, bad.json)

    def test_independent_boards_copy_history_and_disk_restart(self):
        original = self.save({'title': 'night', 'direction': 'short', 'objects': [drawing('fixed', 100)], 'notes': 'wait'}).json['plan']
        copied = self.create({'title': 'next day', 'copy_from': '', 'direction': 'long'})
        identity = copied['board_id']
        self.assertFalse(copied['enabled'])
        self.assertEqual(copied['objects'], original['objects'])
        response = self.owner.post(f'/api/workspace/plans/{SYMBOL}/patch?board={identity}',
            json={'base_revision': 1, 'changes': {'notes': 'separate', 'archived': True}}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.store.plan(SYMBOL)['notes'], 'wait')
        self.assertEqual(self.owner.get('/api/workspace/review?symbol='+SYMBOL+'&board='+identity).json['plan_revision'], 2)
        self.assertEqual(len(self.owner.get('/api/workspace/boards?symbol='+SYMBOL).json['boards']), 2)
        restored = WorkspaceStore(self.path)
        self.assertEqual(restored.plan(board_key(SYMBOL, identity))['notes'], 'separate')
        self.assertEqual(len(restored.history(board_key(SYMBOL, identity))), 2)
        older = self.create({'title': 'restored', 'copy_from': identity, 'copy_revision': 1})
        self.assertEqual(older['notes'], 'wait')
        self.assertFalse(older['archived'])

    def test_locks_reject_patch_put_delete_and_combined_unlock_move(self):
        obj = {**drawing('fixed', 100), 'locked': True}
        self.save({'objects': [obj]})
        moved = {**obj, 'points': [{'time': 1794200000000, 'price': 101}]}
        for change in (moved, {**moved, 'locked': False}):
            self.assertEqual(self.patch_plan(1, upserts=[change]).status_code, 400)
        self.assertEqual(self.patch_plan(1, deletes=['fixed']).status_code, 400)
        self.assertEqual(self.save({'objects': []}, 1).status_code, 400)
        self.assertEqual(self.patch_plan(1, upserts=[{**obj, 'locked': False}]).status_code, 200)
        self.assertEqual(self.patch_plan(2, deletes=['fixed']).status_code, 200)

    def test_wave_ids_labels_survive_refresh_and_reload(self):
        wave = {'id': 'wave', 'kind': 'wave', 'locked': True, 'period': 'm30', 'points': [
            {'id': 'a', 'label': '1', 'time': 1794200000000, 'price': 100},
            {'id': 'c', 'label': '3', 'time': 1794201800000, 'price': 90}]}
        self.assertEqual(self.save({'objects': [wave]}).status_code, 200)
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)['objects'], [wave])
        bad = copy.deepcopy(wave);bad['id'] = 'bad';bad['points'][1]['id'] = 'a'
        self.assertEqual(self.patch_plan(1, upserts=[bad]).status_code, 400)

    def test_comments_append_without_lost_updates_and_cannot_spoof_author(self):
        self.save({'objects': [{**drawing('fixed', 100), 'locked': True}]})
        peer, headers = self.join('Peer')
        endpoint = '/api/workspace/comments?symbol='+SYMBOL
        a = self.owner.post(endpoint, json={'text': 'first', 'object_id': 'fixed'}, headers=self.owner_headers)
        b = peer.post(endpoint, json={'text': 'second'}, headers=headers)
        self.assertEqual(a.status_code, 200);self.assertEqual(b.status_code, 200)
        self.assertEqual([c['author']['name'] for c in b.json['plan']['comments']], ['Owner', 'Peer'])
        self.assertEqual(self.patch_plan(3, changes={'comments': []}).status_code, 400)
        self.assertEqual(self.owner.post(endpoint, json={'text': 'fake', 'author': 'Peer'}, headers=self.owner_headers).status_code, 400)
        result=self.save({'objects': b.json['plan']['objects'], 'comments': []}, 3)
        self.assertEqual(len(result.json['plan']['comments']), 2)

    def test_presence_isolated_by_board_and_bad_ids_rejected(self):
        copied=self.create({'title': 'separate'})
        board=copied['board_id']
        response=self.owner.post('/api/workspace/presence?board='+board, json={'symbol': SYMBOL, 'period': 'm30'}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner.get('/api/workspace/presence?symbol='+SYMBOL).json['presence'], [])
        self.assertEqual(len(self.owner.get('/api/workspace/presence?symbol='+SYMBOL+'&board='+board).json['presence']), 1)
        self.assertEqual(self.owner.get('/api/workspace/plans/'+SYMBOL+'?board=../bad').status_code, 400)

    def test_compact_history_reports_changes_without_resending_every_drawing(self):
        self.save({'objects': [drawing('a', 100)]})
        self.patch_plan(1, changes={'notes': 'changed'}, upserts=[drawing('b', 101)])
        entries=self.owner.get('/api/workspace/history?symbol='+SYMBOL+'&compact=1').json['history']
        self.assertEqual(entries[0]['changes']['added'], 1)
        self.assertIn('notes', entries[0]['changes']['fields'])
        self.assertEqual(entries[0]['plan']['object_count'], 2)
        self.assertNotIn('objects', entries[0]['plan'])


if __name__ == '__main__':
    unittest.main()
