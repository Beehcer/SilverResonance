"""Concurrent field/node edits, durable retries and view awareness regressions."""
import copy
import json
import unittest
from unittest.mock import patch

import test_workspace_api as base
from test_workspace_api import SYMBOL
from workspace_store import WorkspaceStore


def wave():
    return {"id": "wave", "kind": "wave", "color": "#ff0000", "period": "m30", "name": "Wave",
            "points": [{"id": identity, "label": str(index + 1), "time": 1700000000000 + index * 60000, "price": price}
                       for index, (identity, price) in enumerate(zip(["a", "b", "c", "d"], [100, 110, 105, 115]))]}


class WorkspaceCollaborationTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    market = base.WorkspaceApiTests.market
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    join = base.WorkspaceApiTests.join
    save = base.WorkspaceApiTests.save

    def send(self, edits=None, changes=None, deletes=None, mutation=None, client=None, headers=None, revision=1):
        payload = {"base_revision": revision, "conflict_policy": "editor_wins", "object_edits": edits or [],
                   "changes": changes or {}, "delete_object_ids": deletes or []}
        if mutation:
            payload["mutation_id"] = mutation
        return (client or self.owner).post('/api/workspace/plans/' + SYMBOL + '/patch', json=payload,
                                           headers=headers or self.owner_headers)

    def test_same_object_properties_and_distinct_nodes_do_not_overwrite(self):
        self.save({"objects": [wave()]})
        peer, headers = self.join('Peer')
        first = self.send([{"id": "wave", "changes": {"color": "#00ff00"}, "node_updates": [{"id": "a", "changes": {"price": 99}}]}])
        self.assertEqual(first.status_code, 200, first.json)
        second = self.send([{"id": "wave", "changes": {"name": "Peer name", "z_index": 2.5, "source_period": "day"},
                             "node_updates": [{"id": "b", "changes": {"price": 120}}]}], client=peer, headers=headers)
        self.assertEqual(second.status_code, 200, second.json)
        obj = second.json['plan']['objects'][0]
        self.assertEqual((obj['color'], obj['name'], obj['z_index'], obj['source_period']), ('#00ff00', 'Peer name', 2.5, 'day'))
        self.assertEqual([p['price'] for p in obj['points']], [99, 120, 105, 115])

    def test_concurrent_inserts_and_deleted_nodes_are_not_resurrected(self):
        self.save({"objects": [wave()]})
        for identity in ['peer', 'mine']:
            response = self.send([{"id": "wave", "node_inserts": [{"point": {"id": identity, "time": 1700000030000, "price": 108}, "after": "a", "before": "b"}]}])
            self.assertEqual(response.status_code, 200, response.json)
        self.send([{"id": "wave", "node_deletes": ["a"]}])
        stale = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"price": 101}}]}])
        self.assertEqual(stale.status_code, 200, stale.json)
        points = stale.json['plan']['objects'][0]['points']
        self.assertEqual([p['id'] for p in points], ['peer', 'mine', 'b', 'c', 'd'])
        self.assertEqual([p['label'] for p in points], ['1', '2', '3', '4', '5'])
        self.send(deletes=['wave'])
        stale = self.send([{"id": "wave", "changes": {"color": "#ffffff"}}])
        self.assertEqual(stale.json['plan']['objects'], [])

    def test_retry_survives_restart_without_reverting_later_peer_edit(self):
        self.save({"objects": [wave()]})
        edits = [{"id": "wave", "changes": {"color": "#00ff00"}}]
        response = self.send(edits, mutation='request-1')
        self.assertEqual(response.status_code, 200, response.json)
        self.send([{"id": "wave", "changes": {"color": "#0000ff"}}])
        reloaded = WorkspaceStore(self.path)
        current = reloaded.patch_plan(SYMBOL, 1, {}, [], [], self.owner_session['user']['id'], 'rule-v1', 'editor_wins', edits, 'request-1')
        self.assertEqual(current['revision'], 3)
        self.assertEqual(current['objects'][0]['color'], '#0000ff')
        changed = self.send([{"id": "wave", "changes": {"color": "#ffffff"}}], mutation='request-1')
        self.assertEqual(changed.status_code, 400)

    def test_same_field_latest_edit_wins_but_locked_object_stays_locked(self):
        self.save({"objects": [wave()]})
        self.send([{"id": "wave", "changes": {"name": "first"}}])
        response = self.send([{"id": "wave", "changes": {"name": "latest"}}])
        self.assertEqual(response.json['plan']['objects'][0]['name'], 'latest')
        self.send([{"id": "wave", "changes": {"locked": True}}])
        blocked = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"price": 102}}]}])
        self.assertEqual(blocked.status_code, 400)

    def test_invalid_edits_are_atomic_and_minimum_geometry_is_preserved(self):
        self.save({"objects": [wave()]})
        bad_edits = [
            [{"id": "wave", "changes": {"kind": "horizontal"}}],
            [{"id": "wave", "node_deletes": ['a', 'b', 'c']}],
            [{"id": "wave", "node_updates": [{"id": "b", "changes": {"price": -1}}]}],
            [{"id": "wave", "changes": {"points": []}, "node_deletes": ['a']}],
        ]
        for edits in bad_edits:
            self.assertEqual(self.send(edits).status_code, 400, edits)
            self.assertEqual(self.store.plan(SYMBOL)['revision'], 1)

    def test_follow_view_is_validated_transient_and_isolated_to_board(self):
        view = {'period': 'm30', 'range': {'start': 1700000000000, 'end': 1700003600000, 'min': 90, 'max': 120}}
        payload = {'symbol': SYMBOL, 'period': 'm30', 'view': view}
        with patch('workspace_store.time.monotonic', return_value=100):
            response = self.owner.post('/api/workspace/presence', json=payload, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['presence'][0]['view'], view)
        with patch('workspace_store.time.monotonic', return_value=107):
            self.assertIsNone(self.store.presence(SYMBOL)[0]['view'])
            self.assertEqual(self.store.presence(SYMBOL + '::other'), [])
        bad = copy.deepcopy(payload)
        bad['view']['range']['min'] = 130
        self.assertEqual(self.owner.post('/api/workspace/presence', json=bad, headers=self.owner_headers).status_code, 400)
        self.assertNotIn('view', self.path.read_text(encoding='utf-8'))

    def test_existing_schema_two_is_migrated_without_losing_plan(self):
        self.save({'notes': 'keep me', 'objects': [wave()]})
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        saved.pop('mutations')
        self.path.write_text(json.dumps(saved), encoding='utf-8')
        reloaded = WorkspaceStore(self.path)
        self.assertEqual(reloaded.plan(SYMBOL)['notes'], 'keep me')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['mutations'], {})


if __name__ == '__main__':
    unittest.main()
