"""Adversarial offline tests of the guarded orchestration boundary."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).parents[1]


def load_client():
    spec = importlib.util.spec_from_file_location('security_client', ROOT / 'scripts/client.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def music_payload():
    return {'prompt': 'Instrumental test', 'lyrics': '[instrumental]', 'batch_size': 1,
            'audio_duration': 10, 'audio_format': 'wav'}


def wallet_limits():
    return {'tier': 'starter', 'key': {'status': 'ok', 'billing_mode': 'wallet',
            'cap': {'cap_blocked': False, 'spend_cap_rub': 0, 'spent_rub': 0}},
            'wallet': {'balance_rub': 100}}


def prices():
    return {'prices': [{'model': 'music:generate', 'billing': 'unit',
                        'unit_rub': 5, 'premium': False}]}


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x00\x00' * 120)
    return out.getvalue()


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.client = load_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state.json'
        self.output = Path(self.tmp.name) / 'track.wav'
        self.key = patch.object(self.client, 'resolve_keys', return_value=('NEURALDEEP_MUSIC_API_KEY', ['fixture']))
        self.key.start()
        self.addCleanup(self.key.stop)

    def transport(self, limits=None, submit=None, status=None, result=None):
        calls = []
        def request(method, url, key, body=None, content_type="application/json", **kwargs):
            calls.append((method, url))
            if url.endswith('/limits'):
                return wallet_limits() if limits is None else limits
            if 'wallet-prices' in url:
                return prices()
            if method == 'POST':
                if isinstance(submit, Exception):
                    raise submit
                return {'task_id': 'safe_job', 'status': 'queued'} if submit is None else submit
            if '/result?' in url:
                return (wav_bytes(), 'audio/wav') if result is None else result
            if '/tasks/' in url:
                stored = json.loads(self.state.read_text(encoding='utf-8'))
                self.assertEqual(stored['provider_job_id'], 'safe_job', 'job ID must be durable before polling')
                return {'status': 'succeeded', 'tracks': [{}]} if status is None else status
            return {}
        return calls, request

    def run_music(self, approval=5):
        return self.client.run('music', 'generate', music_payload(), self.state, self.output, approval, 5)

    def test_missing_spend_cap_blocks_before_post(self):
        limits = wallet_limits()
        del limits['key']['cap']
        calls, transport = self.transport(limits=limits)
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music()
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(any(method == 'POST' for method, _ in calls))

    def test_wrong_cap_type_blocks_before_post(self):
        limits = wallet_limits()
        limits['key']['cap']['cap_blocked'] = 'false'
        calls, transport = self.transport(limits=limits)
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music()
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(any(method == 'POST' for method, _ in calls))

    def test_key_cap_below_quote_blocks_before_post(self):
        limits = wallet_limits()
        limits['key']['cap']['spend_cap_rub'] = 1
        calls, transport = self.transport(limits=limits)
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music()
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(any(method == 'POST' for method, _ in calls))

    def test_no_approval_creates_no_submission_state(self):
        calls, transport = self.transport()
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music(approval=None)
        self.assertEqual(result['status'], 'wallet_approval_required')
        self.assertFalse(any(method == 'POST' for method, _ in calls))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.output.exists())

    def test_insufficient_approved_amount_blocks_before_post(self):
        calls, transport = self.transport()
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music(approval=4.99)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(any(method == 'POST' for method, _ in calls))

    def test_existing_state_blocks_before_network(self):
        self.state.write_text('{}', encoding='utf-8')
        with patch.object(self.client, 'request') as request:
            result = self.run_music()
        self.assertEqual(result['status'], 'blocked')
        request.assert_not_called()

    def test_missing_validator_does_not_enable_a_fallback(self):
        with patch.object(self.client, 'load_service_api', return_value=None), \
                patch.object(self.client, 'request') as request:
            result = self.run_music()
        self.assertEqual(result['status'], 'blocked')
        request.assert_not_called()

    def test_real_transport_failure_after_post_is_uncertain_not_a_retry(self):
        calls, transport = self.transport(submit=self.client.base.RequestError('Transport or response error'))
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music()
        self.assertEqual(result['status'], 'reconciliation_required')
        self.assertEqual(sum(method == 'POST' for method, _ in calls), 1)
        self.assertTrue(self.state.is_file())
        self.assertFalse(self.output.exists())

    def test_invalid_binary_result_keeps_job_for_resume(self):
        calls, transport = self.transport(result=(b'{"detail":"private-provider-text"}', 'application/json'))
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music()
        self.assertEqual(result['status'], 'poll_error')
        self.assertEqual(result['provider_job_id'], 'safe_job')
        self.assertNotIn('private-provider-text', json.dumps(result))
        self.assertEqual(sum(method == 'POST' for method, _ in calls), 1)
        self.assertFalse(self.output.exists())

    def test_failed_job_never_retrieves_a_result(self):
        calls, transport = self.transport(status={'status': 'failed'})
        with patch.object(self.client, 'request', side_effect=transport):
            result = self.run_music()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(any('/result?' in url for _, url in calls))
        self.assertEqual(result['provider_job_id'], 'safe_job')

    def test_music_wav_must_be_decodable_not_just_a_riff_prefix(self):
        with self.assertRaises(ValueError):
            self.client.validate_result('music', 'generate', music_payload(), b'RIFF' + b'\x00' * 100, 'audio/wav')

    def test_valid_music_wav_is_preserved(self):
        data = wav_bytes()
        self.assertEqual(self.client.validate_result('music', 'generate', music_payload(), data, 'audio/wav'), data)


if __name__ == '__main__':
    unittest.main()
