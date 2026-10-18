from __future__ import annotations

import json
import os
import sys
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import test_harness
from embryo_harness import core, submissions
from embryo_harness.files import HarnessError, read_json, record, write_json


def multipart(request):
    message = BytesParser(policy=policy.default).parsebytes(
        b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + request.read())
    return {p.get_param('name', header='content-disposition'): p.get_payload(decode=True)
            for p in message.iter_parts()}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_harness.HarnessTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.run = self.fixture.prepare()
        core.execute(self.run)
        self.bundle = core.bundle(self.run)
        self.manifest = read_json(self.bundle / 'manifest.json')
        self.sha = self.manifest['predictions'][0]['sha256']
        self.requests = []
        self.evidence = []
        self.uploads = []
        self.retry_official = False
        self.timeout_evidence = False
        self.unknown_evidence = False
        self.closed = False
        self.reject = False
        self.wrong_hash = False
        self.stale = False
        self.bad_format = False
        self.account = {'registered': True, 'track': 'agent', 'team_id': 'team-1',
                        'team_name': 'Cosmic Embryos'}
        self.reclaimed = False
        self.status_override = None
        self.evidence_note = None
        self.env = patch.dict(os.environ, {'VE_CHALLENGE_API_KEY': 'test-secret-never-log'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = submissions.API(transport=httpx.MockTransport(self.handle), sleeper=lambda _: None)
        self.addCleanup(self.api.close)

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path in ('/challenge/phase', '/challenge/boards', '/challenge/documents'):
            self.assertNotIn('authorization', request.headers)
        else:
            self.assertEqual(request.headers.get('authorization'), 'Bearer ' + self.api.key)
        if path == '/challenge/phase':
            body = {'accepts_submissions': not self.closed, 'phase': 'p2'}
        elif path == '/challenge/boards':
            body = {'boards': [{'key': 'T1:val'}]}
        elif path == '/challenge/documents':
            body = {'rules': 'v1', 'terms': 'v1'}
        elif path == '/challenge/me':
            body = dict(self.account, stale_consents=['rules'] if self.stale else [])
        elif path == '/challenge/integration/quota':
            body = {'daily': {'T1': {'used': 0, 'limit': 8, 'uploads': 0, 'upload_limit': 20}}}
        elif path == '/challenge/submissions' and request.method == 'GET':
            body = {'registered': True, 'submissions': [dict(self.detail(),
                    reclaimed_at='2026-10-18T00:00:00Z' if self.reclaimed else None)]}
        elif path == '/challenge/submissions':
            fields = multipart(request)
            if fields.get('format_only') == b'true':
                self.assertNotIn('idempotency-key', request.headers)
                body = {'ok': not self.bad_format, 'format_only': True, 'problems': []}
            else:
                self.assertNotIn('format_only', fields)
                self.assertEqual(fields['agent_model'], b'test-model')
                self.uploads.append((request.headers['idempotency-key'], fields))
                if self.retry_official and len(self.uploads) == 1:
                    return httpx.Response(503, json={'detail': 'temporary'})
                body = self.detail()
        elif path == '/challenge/submissions/sub-1':
            body = self.detail()
        elif path == '/challenge/teams/evidence/sub-1':
            if request.method == 'POST':
                fields = multipart(request)
                row = {'kind': fields['kind'].decode(), 'note': fields['note'].decode()}
                if self.unknown_evidence:
                    raise httpx.ReadTimeout('unknown outcome')
                self.evidence.append(row)
                if self.timeout_evidence:
                    self.timeout_evidence = False
                    raise httpx.ReadTimeout('accepted but response lost')
                body = {'queued': len(self.evidence) >= 2}
                if self.evidence_note:
                    body.update(queued=False, note=self.evidence_note)
            else:
                body = {'files': self.evidence, 'kinds': sorted({f['kind'] for f in self.evidence}),
                        'required': True, 'min_kinds': 2, 'required_kind': 'trajectory',
                        'max_bytes': 200_000_000, 'max_total_bytes': 600_000_000}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)

    def detail(self):
        return {'id': 'sub-1', 'bundle_key': 'T1:val',
                'sha256': 'wrong' if self.wrong_hash else self.sha,
                'status': 'rejected' if self.reject else self.status_override or
                          ('queued' if len(self.evidence) >= 2 else 'awaiting_evidence'),
                'problems': ['bad'] if self.reject else []}

    def submit(self, **kwargs):
        return submissions.submit(self.root, self.bundle, 'T1:val', self.api, method='Synthetic fixture', **kwargs)

    def test_submit_seals_identity_and_attaches_three_kinds(self):
        result = self.submit()
        self.assertTrue(result['evidence_complete'])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual({f['kind'] for f in self.evidence}, set(submissions.EVIDENCE_KINDS))
        self.submit()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(len(self.evidence), 3)
        self.assertNotIn('test-secret', Path(result['state_file']).read_text())
        self.assertEqual(read_json(Path(result['state_file']))['account_binding'],
                         {'team_id': 'team-1', 'track': 'agent'})

    def test_invalid_account_blocks_all_uploads(self):
        for changes in ({'registered': False}, {'track': 'human'}, {'track': None},
                        {'team_id': None}, {'team_name': 'Other Team'}):
            with self.subTest(changes=changes), patch.dict(self.account, changes):
                with self.assertRaises(HarnessError):
                    self.submit()
                self.assertFalse(any(r.method == 'POST' for r in self.requests))

    def test_changed_team_blocks_uncertain_official_retry(self):
        original = self.api.request
        def lost_response(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if kwargs.get('request_id'):
                raise submissions.ApiError('Accepted but response lost')
            return result
        with patch.object(self.api, 'request', side_effect=lost_response):
            with self.assertRaises(submissions.ApiError): self.submit()
        path = submissions.state_path(self.root, self.run.name, 'T1:val')
        saved = read_json(path)
        self.account['team_id'] = 'team-2'
        with self.assertRaisesRegex(HarnessError, 'saved submission account'): self.submit()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(read_json(path), saved)

    def test_automatic_retry_rechecks_team_binding(self):
        self.retry_official = True
        self.api.sleeper = lambda _: self.account.update(team_id='team-2')
        with self.assertRaisesRegex(HarnessError, 'saved submission account'): self.submit()
        self.assertEqual(len(self.uploads), 1)

    def test_confirmed_submission_cannot_resume_evidence_with_other_team(self):
        self.timeout_evidence = True
        with self.assertRaises(submissions.ApiError): self.submit()
        self.account['team_id'] = 'team-2'
        with self.assertRaisesRegex(HarnessError, 'saved submission account'): self.submit()
        self.assertEqual(len(self.evidence), 1)
        self.assertEqual(len(self.uploads), 1)

    def test_same_team_key_rotation_can_resume_evidence(self):
        self.timeout_evidence = True
        with self.assertRaises(submissions.ApiError): self.submit()
        self.api.key = 'rotated-test-secret'
        self.assertTrue(self.submit()['evidence_complete'])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(len(self.evidence), 3)

    def test_legacy_state_without_team_binding_stops_before_mutation(self):
        result = self.submit()
        path = Path(result['state_file'])
        saved = read_json(path)
        del saved['account_binding']
        write_json(path, saved)
        with self.assertRaisesRegex(HarnessError, 'no team binding'): self.submit()
        self.assertEqual(read_json(path), saved)
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(len(self.evidence), 3)

    def test_reclaimed_prediction_in_history_blocks_evidence(self):
        self.reclaimed = True
        with self.assertRaisesRegex(HarnessError, 'reclaimed'): self.submit()
        self.assertEqual(self.evidence, [])
        state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.assertFalse(state['evidence_complete'])
        self.assertIsNotNone(state['history_entry']['reclaimed_at'])

    def test_reclaimed_prediction_note_is_retained_and_blocks_resume(self):
        self.evidence_note = 'Prediction file was reclaimed; make a new prediction submission.'
        with self.assertRaisesRegex(HarnessError, 'reclaimed'): self.submit()
        self.assertEqual(len(self.evidence), 1)
        state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.assertFalse(state['evidence_complete'])
        self.assertEqual(state['evidence']['trajectory']['response']['note'], self.evidence_note)
        with self.assertRaisesRegex(HarnessError, 'server note'): self.submit()
        self.assertEqual(len(self.evidence), 1)
        self.assertEqual(len(self.uploads), 1)

    def test_reclamation_during_evidence_is_caught_by_final_history_check(self):
        original = self.api.request
        def reclaim(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if method == 'POST' and '/evidence/' in route:
                self.reclaimed = True
            return result
        with patch.object(self.api, 'request', side_effect=reclaim):
            with self.assertRaisesRegex(HarnessError, 'reclaimed'): self.submit()
        state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.assertFalse(state['evidence_complete'])

    def test_final_nonready_status_does_not_report_success(self):
        for status in ('awaiting_evidence', 'cancelled', 'rejected', 'eligibility_review'):
            with self.subTest(status=status):
                self.status_override = None
                original = self.api.request
                detail_reads = 0
                def change_status(method, route, **kwargs):
                    nonlocal detail_reads
                    if method == 'GET' and route == '/challenge/submissions/sub-1':
                        detail_reads += 1
                        if detail_reads == 2:
                            self.status_override = status
                    return original(method, route, **kwargs)
                with patch.object(self.api, 'request', side_effect=change_status):
                    with self.assertRaises(HarnessError): self.submit()
                state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
                self.assertFalse(state['evidence_complete'])
                self.assertEqual(detail_reads, 2)
        self.status_override = None
        self.assertTrue(self.submit()['evidence_complete'])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(len(self.evidence), 3)

    def test_queued_false_alone_does_not_reject_an_already_queued_job(self):
        original = self.api.request
        def no_transition(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if method == 'POST' and '/evidence/' in route:
                result['queued'] = False
            return result
        with patch.object(self.api, 'request', side_effect=no_transition):
            result = self.submit()
        self.assertTrue(result['evidence_complete'])
        self.assertEqual(result['status'], 'queued')

    def test_old_scored_submission_absent_from_recent_history_is_supported(self):
        self.submit()
        self.status_override = 'scored'
        original = self.api.request
        def old_history(method, route, **kwargs):
            if method == 'GET' and route == '/challenge/submissions':
                return {'registered': True, 'submissions': []}
            return original(method, route, **kwargs)
        with patch.object(self.api, 'request', side_effect=old_history):
            result = self.submit()
        self.assertTrue(result['evidence_complete'])
        self.assertEqual(result['status'], 'scored')

    def test_official_retry_preserves_idempotency_and_payload(self):
        self.retry_official = True
        self.submit()
        self.assertEqual(len(self.uploads), 2)
        self.assertEqual(self.uploads[0], self.uploads[1])

    def test_uncertain_official_replay_stops_after_closed_phase(self):
        original = self.api.request
        def fail(method, route, **kwargs):
            if kwargs.get('request_id'):
                raise submissions.ApiError('connection lost')
            return original(method, route, **kwargs)
        with patch.object(self.api, 'request', side_effect=fail):
            with self.assertRaises(submissions.ApiError): self.submit()
        state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.closed = True
        with self.assertRaisesRegex(HarnessError, 'closed.*preserve the request ID'):
            self.submit()
        self.assertEqual(self.uploads, [])
        saved = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.assertEqual(saved['request_id'], state['request_id'])
        self.closed = False
        self.submit()
        self.assertEqual(self.uploads[0][0], state['request_id'])

    def test_automatic_retry_stops_when_phase_changes_even_if_board_stays_active(self):
        self.retry_official = True
        original = self.api.request
        def changed_phase(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if route == '/challenge/phase' and self.uploads:
                result['phase'] = 'p3'
            return result
        with patch.object(self.api, 'request', side_effect=changed_phase):
            with self.assertRaisesRegex(HarnessError, 'phase has changed'):
                self.submit()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(self.evidence, [])

    def test_automatic_retry_stops_when_board_changes(self):
        self.retry_official = True
        original = self.api.request
        def changed_board(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if route == '/challenge/boards' and self.uploads:
                result['boards'] = [{'key': 'T1:test'}]
            return result
        with patch.object(self.api, 'request', side_effect=changed_board):
            with self.assertRaisesRegex(HarnessError, 'no longer active'):
                self.submit()
        self.assertEqual(len(self.uploads), 1)

    def test_legacy_uncertain_request_without_phase_requires_inspection(self):
        self.retry_official = True
        def fail_after_first_attempt(_):
            raise submissions.ApiError('interrupted')
        self.api.sleeper = fail_after_first_attempt
        with self.assertRaises(submissions.ApiError):
            self.submit()
        path = submissions.state_path(self.root, self.run.name, 'T1:val')
        state = read_json(path)
        del state['phase_before']
        write_json(path, state)
        with self.assertRaisesRegex(HarnessError, 'phase is unknown'):
            self.submit()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(read_json(path)['request_id'], state['request_id'])

    def test_confirmed_submission_can_finish_evidence_after_phase_closes(self):
        self.timeout_evidence = True
        with self.assertRaises(submissions.ApiError):
            self.submit()
        self.closed = True
        result = self.submit()
        self.assertTrue(result['evidence_complete'])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(len(self.evidence), 3)

    def test_conflicting_remote_hash_is_not_overridden_by_note_or_attached_state(self):
        result = self.submit()
        self.evidence[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(HarnessError, 'identity cannot be confirmed'):
            self.submit()
        self.assertFalse(read_json(Path(result['state_file']))['evidence_complete'])
        self.assertEqual(len(self.evidence), 3)

    def test_matching_remote_hash_is_authoritative_over_changed_note(self):
        self.submit()
        for row in self.evidence:
            row['sha256'] = self.manifest['evidence'][row['kind'] + '.tar.gz']['sha256']
            row['note'] = 'Edited display note'
        self.assertTrue(self.submit()['evidence_complete'])
        self.assertEqual(len(self.evidence), 3)

    def test_remote_kinds_alone_cannot_confirm_previously_attached_evidence(self):
        result = self.submit()
        self.evidence[0].pop('note')
        with self.assertRaisesRegex(HarnessError, 'identity cannot be confirmed'):
            self.submit()
        self.assertFalse(read_json(Path(result['state_file']))['evidence_complete'])
        self.assertEqual(len(self.evidence), 3)

    def test_uncertain_evidence_with_conflicting_hash_is_not_reconciled_by_note(self):
        self.timeout_evidence = True
        with self.assertRaises(submissions.ApiError):
            self.submit()
        self.evidence[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(HarnessError, 'Uncertain'):
            self.submit()
        self.assertEqual(len(self.evidence), 1)

    def test_final_evidence_scan_verifies_identity_again(self):
        original = self.api.request
        def changed_evidence(method, route, **kwargs):
            if method == 'GET' and '/evidence/' in route and len(self.evidence) == 3:
                self.evidence[0]['sha256'] = '0' * 64
            return original(method, route, **kwargs)
        with patch.object(self.api, 'request', side_effect=changed_evidence):
            with self.assertRaisesRegex(HarnessError, 'file identities'):
                self.submit()
        state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.assertFalse(state['evidence_complete'])
        self.assertEqual(len(self.evidence), 3)

    def test_evidence_timeout_reconciles_without_duplicate(self):
        self.timeout_evidence = True
        with self.assertRaises(submissions.ApiError): self.submit()
        self.assertEqual(len(self.evidence), 1)
        self.submit()
        self.assertEqual(len(self.evidence), 3)
        self.assertEqual(len(self.uploads), 1)

    def test_ambiguous_evidence_is_not_blindly_retried(self):
        self.unknown_evidence = True
        with self.assertRaises(submissions.ApiError): self.submit()
        posts = sum(r.method == 'POST' and '/evidence/' in r.url.path for r in self.requests)
        with self.assertRaisesRegex(HarnessError, 'Uncertain'): self.submit()
        self.assertEqual(posts, sum(r.method == 'POST' and '/evidence/' in r.url.path for r in self.requests))

    def test_rejected_200_is_not_success(self):
        self.reject = True
        with self.assertRaisesRegex(HarnessError, 'rejected'): self.submit()
        self.assertEqual(self.evidence, [])

    def test_server_hash_mismatch_blocks_evidence(self):
        self.wrong_hash = True
        with self.assertRaisesRegex(HarnessError, 'board/hash'): self.submit()
        self.assertEqual(self.evidence, [])

    def test_modified_export_cannot_be_uploaded(self):
        (self.bundle / 'prediction-1.h5ad').write_bytes(b'changed')
        with self.assertRaisesRegex(HarnessError, 'differs'): self.submit()
        self.assertEqual(self.requests, [])

    def test_board_fields_are_derived_from_frozen_contract(self):
        cases = [('T2:embryo:val_interp', 'T2', 'embryo', 'val_interp', 'interp'),
                 ('T2:heart:val_interp', 'T2', 'heart', 'val_interp', 'interp'),
                 ('T2:heart:val_extrap', 'T2', 'heart', 'val_extrap', 'extrap'),
                 ('T3:gata4', 'T3', None, 'val', None)]
        for board, task, setting, split, mode in cases:
            with self.subTest(board=board):
                test_harness.h5ad(self.fixture.data, coords=True)
                spec = dict(self.fixture.index['T1:val'], task=task, needs_coords=True,
                            setting=setting, split=split)
                write_json(self.fixture.panels / 'index.json', {board: spec})
                write_json(self.fixture.panels / 'provenance.json', {'files': {
                    name: record(self.fixture.panels / name) for name in ('index.json', 'genes.txt')}})
                self.fixture.config.update(task=task, boards=[board], targets={board: 'fixture'})
                write_json(self.fixture.config_path, self.fixture.config)
                run = self.fixture.prepare()
                core.execute(run)
                bundle = core.bundle(run)
                fields = submissions.load_bundle(self.root, bundle, board)[3]
                self.assertEqual(fields['task'], task)
                if task == 'T2':
                    self.assertEqual(fields['setting'], setting)
                    self.assertEqual(fields['mode'], mode)
                else:
                    self.assertNotIn('setting', fields)
                    self.assertNotIn('mode', fields)

    def test_current_evidence_limit_is_checked_before_upload(self):
        original = self.api.request
        def small_limit(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if method == 'GET' and '/evidence/' in route:
                result['max_bytes'] = 1
            return result
        with patch.object(self.api, 'request', side_effect=small_limit):
            with self.assertRaisesRegex(HarnessError, 'evidence limits'): self.submit()
        self.assertEqual(self.evidence, [])

    def test_changed_retry_fields_are_rejected(self):
        self.submit()
        with self.assertRaisesRegex(HarnessError, 'Retry must'):
            self.submit(architecture='changed')
        self.assertEqual(len(self.uploads), 1)

    def test_stale_consents_block_upload(self):
        self.stale = True
        with self.assertRaisesRegex(HarnessError, 'Rules/Terms'): self.submit()
        self.assertEqual(self.uploads, [])

    def test_failed_format_check_spends_no_official_attempt(self):
        self.bad_format = True
        with self.assertRaisesRegex(HarnessError, 'format check failed'): self.submit()
        self.assertEqual(self.uploads, [])

    def test_phase_change_blocks_new_upload(self):
        self.closed = True
        with self.assertRaisesRegex(HarnessError, 'closed'): self.submit()
        self.assertEqual(self.uploads, [])

    def test_public_readiness_needs_no_key(self):
        self.api.key = ''
        result = submissions.readiness(self.api, public_only=True)
        self.assertIn('phase', result)
        with self.assertRaisesRegex(HarnessError, 'VE_CHALLENGE_API_KEY'):
            submissions.readiness(self.api)

    def test_local_key_file_and_environment_precedence(self):
        local = self.root / 'ai/.env.local'
        local.write_text('# Private local configuration\nVE_CHALLENGE_API_KEY="file-secret"\n')
        with patch.dict(os.environ, {'VE_CHALLENGE_API_KEY': ''}):
            self.assertEqual(submissions.api_key(self.root), 'file-secret')
        self.assertEqual(submissions.api_key(self.root), 'test-secret-never-log')
        self.assertNotIn(local, list(core.source_files(self.root / 'ai')))

    def test_local_key_file_never_executes_shell_syntax(self):
        local = self.root / 'ai/.env.local'
        local.write_text('VE_CHALLENGE_API_KEY=$(touch should-not-exist)\n')
        with patch.dict(os.environ, {'VE_CHALLENGE_API_KEY': ''}):
            with self.assertRaisesRegex(HarnessError, 'Invalid'):
                submissions.api_key(self.root)
        self.assertFalse((self.root / 'should-not-exist').exists())

    def test_evidence_401_allows_explicit_retry_after_credentials_fixed(self):
        original = self.api.request
        def fail(method, route, **kwargs):
            if method == 'POST' and '/evidence/' in route:
                raise submissions.ApiError('Expired key', status_code=401)
            return original(method, route, **kwargs)
        with patch.object(self.api, 'request', side_effect=fail):
            with self.assertRaises(submissions.ApiError): self.submit()
        self.submit()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(len(self.evidence), 3)

    def test_status_saves_response_and_checks_identity(self):
        self.submit()
        result = submissions.status(self.root, self.run.name, 'T1:val', self.api)
        self.assertEqual(result['id'], 'sub-1')
        self.wrong_hash = True
        with self.assertRaisesRegex(HarnessError, 'board/hash'):
            submissions.status(self.root, self.run.name, 'T1:val', self.api)

    def test_status_stops_immediately_for_reclaimed_prediction(self):
        self.submit()
        self.status_override = 'awaiting_evidence'
        self.reclaimed = True
        before = len(self.requests)
        with patch.object(self.api, 'sleeper') as sleeper:
            result = submissions.status(self.root, self.run.name, 'T1:val', self.api, wait_seconds=60)
            sleeper.assert_not_called()
        self.assertEqual(result['polling_stopped'], 'prediction_reclaimed')
        self.assertIsNotNone(result['reclaimed_at'])
        self.assertEqual(result['status'], 'awaiting_evidence')
        self.assertEqual([r.url.path for r in self.requests[before:]],
                         ['/challenge/submissions/sub-1', '/challenge/submissions'])
        state = read_json(submissions.state_path(self.root, self.run.name, 'T1:val'))
        self.assertEqual(state['reclaimed_at'], result['reclaimed_at'])
        self.assertEqual(state['history_entry']['reclaimed_at'], result['reclaimed_at'])
        self.assertFalse(state['evidence_complete'])
        self.assertIn('last_checked_at', state)

    def test_status_detects_reclamation_during_polling(self):
        self.submit()
        clock = [0.0]
        waits = []
        def advance(seconds):
            waits.append(seconds)
            clock[0] += seconds
            self.reclaimed = True
        with patch.object(self.api, 'sleeper', side_effect=advance), \
                patch('embryo_harness.submissions.time.monotonic', side_effect=lambda: clock[0]):
            result = submissions.status(self.root, self.run.name, 'T1:val', self.api, wait_seconds=60)
        self.assertEqual(waits, [20])
        self.assertEqual(result['polling_stopped'], 'prediction_reclaimed')

    def test_status_detects_reclamation_in_detail_without_history_entry(self):
        self.submit()
        original = self.api.request
        def detail_only(method, route, **kwargs):
            result = original(method, route, **kwargs)
            if route == '/challenge/submissions/sub-1':
                result['reclaimed_at'] = '2026-10-18T00:00:00Z'
            elif route == '/challenge/submissions':
                result['submissions'] = []
            return result
        with patch.object(self.api, 'request', side_effect=detail_only), \
                patch.object(self.api, 'sleeper') as sleeper:
            result = submissions.status(self.root, self.run.name, 'T1:val', self.api, wait_seconds=60)
            sleeper.assert_not_called()
        self.assertEqual(result['polling_stopped'], 'prediction_reclaimed')

    def test_status_retains_confirmed_reclamation_after_history_rolls_over(self):
        self.submit()
        self.reclaimed = True
        first = submissions.status(self.root, self.run.name, 'T1:val', self.api)
        original = self.api.request
        def empty_history(method, route, **kwargs):
            if route == '/challenge/submissions':
                return {'registered': True, 'submissions': []}
            return original(method, route, **kwargs)
        with patch.object(self.api, 'request', side_effect=empty_history), \
                patch.object(self.api, 'sleeper') as sleeper:
            result = submissions.status(self.root, self.run.name, 'T1:val', self.api, wait_seconds=60)
            sleeper.assert_not_called()
        self.assertEqual(result['reclaimed_at'], first['reclaimed_at'])
        self.assertEqual(result['polling_stopped'], 'prediction_reclaimed')

    def test_status_missing_history_entry_does_not_invent_reclamation(self):
        self.submit()
        original = self.api.request
        def empty_history(method, route, **kwargs):
            if route == '/challenge/submissions':
                return {'registered': True, 'submissions': []}
            return original(method, route, **kwargs)
        with patch.object(self.api, 'request', side_effect=empty_history):
            result = submissions.status(self.root, self.run.name, 'T1:val', self.api)
        self.assertEqual(result['status'], 'queued')
        self.assertIsNone(result['reclaimed_at'])
        self.assertNotIn('polling_stopped', result)

    def test_status_returns_terminal_states_without_waiting(self):
        self.submit()
        for status in submissions.TERMINAL:
            with self.subTest(status=status), patch.object(self.api, 'sleeper') as sleeper:
                self.status_override = status
                result = submissions.status(self.root, self.run.name, 'T1:val', self.api, wait_seconds=60)
                self.assertEqual(result['status'], status)
                self.assertNotIn('polling_stopped', result)
                sleeper.assert_not_called()

    def test_auth_redirect_not_followed_and_secret_redacted(self):
        for code in (302, 401):
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(code, headers={'Location': 'https://example.org'},
                                      json={'detail': self.api.key})
            api = submissions.API(transport=httpx.MockTransport(handle), sleeper=lambda _: None)
            try:
                with self.assertRaises(submissions.ApiError) as error:
                    api.request('GET', '/challenge/me')
                self.assertNotIn(self.api.key, str(error.exception))
                self.assertEqual(len(calls), 1)
            finally:
                api.close()
