"""Offline client integration tests with valid requests and bounded fake replies."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_security_regressions import load_client, music_payload, prices, wallet_limits, wav_bytes


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = load_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state.json'
        self.output = Path(self.tmp.name) / 'track.wav'
        self.calls = []

    def keys(self):
        return patch.object(self.client, 'resolve_keys', return_value=('NEURALDEEP_MUSIC_API_KEY', ['fixture']))

    def transport(self, method, url, key, body=None, content_type='application/json', **kwargs):
        self.calls.append((method, url, key))
        if url.endswith('/limits'): return wallet_limits()
        if 'wallet-prices' in url: return prices()
        if method == 'POST':
            state = json.loads(self.state.read_text(encoding='utf-8'))
            self.assertEqual(state['status'], 'reconciliation_required')
            self.assertEqual(stat_mode(self.state), 0o600)
            self.assertEqual(json.loads(body)['audio_format'], 'wav')
            return {'task_id':'safe_job', 'status':'queued', 'poll':'https://untrusted.example/elsewhere'}
        if '/result?' in url: return wav_bytes(), 'audio/wav'
        if '/tasks/' in url:
            state = json.loads(self.state.read_text(encoding='utf-8'))
            self.assertEqual(state['provider_job_id'], 'safe_job')
            return {'status':'succeeded', 'tracks':[{'url':'https://untrusted.example/audio'}]}
        raise AssertionError('Unexpected mock request')

    def run_music(self, approval=5, payload=None, index=None):
        return self.client.run('music','generate',payload or music_payload(),self.state,self.output,approval,5,index)

    def test_service_default_matches_own_source(self):
        self.assertEqual(self.client.SERVICE, Path(__file__).parents[1].name.removeprefix('neuraldeep-'))

    def test_key_variables_and_operations(self):
        self.assertEqual(self.client.KEY_VARIABLES['drift'],'NEURALDEEP_DRIFT_API_TOKEN')
        self.assertEqual(self.client.OPERATIONS['books'],('list','toc','chapter','search'))
        self.assertEqual(len(self.client.OPERATIONS),9)

    def test_own_environment_precedes_shared_without_dotenv(self):
        fixture={'NEURALDEEP_CHAT_API_KEY':'fixture-own','NEURALDEEP_API_KEY':'fixture-shared'}
        with patch.dict(os.environ, fixture, clear=True), patch.object(self.client.base,'read_dotenv') as dotenv:
            self.assertEqual(self.client.resolve_keys('chat'),('NEURALDEEP_CHAT_API_KEY',['fixture-own']))
        dotenv.assert_not_called()

    def test_own_dotenv_precedes_shared_environment(self):
        with patch.dict(os.environ, {'CODDY_HOME':'/nonexistent','NEURALDEEP_API_KEY':'fixture-shared'}, clear=True), \
             patch.object(self.client.base,'read_dotenv',return_value={'NEURALDEEP_CHAT_API_KEY':'fixture-own'}):
            self.assertEqual(self.client.resolve_keys('chat'),('NEURALDEEP_CHAT_API_KEY',['fixture-own']))

    def test_drift_never_falls_back_to_hub_key(self):
        with patch.dict(os.environ, {'CODDY_HOME':'/nonexistent','NEURALDEEP_API_KEY':'fixture-shared'}, clear=True), \
             patch.object(self.client.base,'read_dotenv',return_value={}):
            with self.assertRaises(self.client.Blocked): self.client.resolve_keys('drift')

    def test_success_persists_wav_and_provenance_without_keys_or_links(self):
        with self.keys(), patch.object(self.client,'request',side_effect=self.transport):
            result=self.run_music()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['provider_job_id'],'safe_job')
        self.assertEqual(self.output.read_bytes(),wav_bytes())
        artifact=result['artifacts'][0]
        self.assertEqual(artifact['sha256'],hashlib.sha256(wav_bytes()).hexdigest())
        self.assertEqual(artifact['provenance']['trust'],'untrusted_data')
        self.assertNotIn('fixture',json.dumps(result))
        self.assertFalse(any('untrusted.example' in url for _,url,_ in self.calls))

    def test_resume_is_get_only_and_uses_pinned_key(self):
        with self.keys(), patch.object(self.client,'request',side_effect=self.transport):
            self.assertEqual(self.run_music()['status'],'completed')
            self.calls.clear()
            result=self.client.resume('music','generate',self.state,Path(self.tmp.name)/'second.wav',5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(result['artifacts']),2)
        self.assertTrue(self.calls)
        self.assertTrue(all(method=='GET' and key=='fixture' for method,_,key in self.calls))
        self.assertFalse(any('/limits' in url or 'wallet-prices' in url for _,url,_ in self.calls))

    def test_unknown_pinned_key_blocks_resume_without_network(self):
        with self.keys(), patch.object(self.client,'request',side_effect=self.transport): self.run_music()
        with patch.object(self.client,'resolve_keys',return_value=('NEURALDEEP_API_KEY',['different'])), \
             patch.object(self.client,'request') as request:
            result=self.client.resume('music','generate',self.state,Path(self.tmp.name)/'second.wav',5)
        self.assertEqual(result['status'],'blocked')
        request.assert_not_called()

    def test_timeout_keeps_submitted_job_id(self):
        with self.keys(), patch.object(self.client,'request',side_effect=self.transport), \
             patch.object(self.client,'poll_music',side_effect=self.client.PollTimeout('Deadline reached')):
            result=self.run_music()
        self.assertEqual(result['status'],'timed_out')
        self.assertEqual(result['provider_job_id'],'safe_job')
        self.assertEqual(sum(method=='POST' for method,_,_ in self.calls),1)
        self.assertFalse(self.output.exists())

    def test_completed_response_after_deadline_is_still_a_timeout(self):
        with patch.object(self.client.time,'monotonic',side_effect=[0,0,2]):
            with self.assertRaises(self.client.PollTimeout):
                self.client.poll_music(lambda *a,**k: {'status':'succeeded'},'safe_job',timeout=1)

    def test_unknown_job_id_is_not_followed_or_retried(self):
        def transport(method,url,key,*args,**kwargs):
            if method=='POST':
                self.calls.append((method,url,key))
                return {'task_id':'../other'}
            return self.transport(method,url,key,*args,**kwargs)
        with self.keys(), patch.object(self.client,'request',side_effect=transport): result=self.run_music()
        self.assertEqual(result['status'],'reconciliation_required')
        self.assertIsNone(result['provider_job_id'])
        self.assertEqual(sum(method=='POST' for method,_,_ in self.calls),1)
        self.assertFalse(any('/tasks/' in url for _,url,_ in self.calls))

    def test_invalid_batch_index_blocks_before_network(self):
        with self.keys(), patch.object(self.client,'request') as request: result=self.run_music(index=1)
        self.assertEqual(result['status'],'blocked')
        request.assert_not_called()

    def test_subscription_chat_readiness_does_not_enable_music(self):
        limits={'tier':'starter','key':{'status':'ok','billing_mode':'subscription'},'decision':{'can_request':True}}
        with self.keys(), patch.object(self.client,'request',return_value=limits) as request: result=self.run_music()
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.args[0],'GET')
        self.assertFalse(self.state.exists())

    def test_valid_drift_payload_is_blocked_before_key_resolution(self):
        payload={'messages':[{'role':'user','content':'Explain only.'}],'max_tokens':16}
        with patch.object(self.client,'resolve_keys') as keys, patch.object(self.client,'request') as request:
            result=self.client.run('drift','chat',payload,self.state,self.output,None,5)
        self.assertEqual(result['status'],'blocked')
        keys.assert_not_called()
        request.assert_not_called()

    def test_token_billed_wallet_cannot_be_unlocked_with_large_approval(self):
        payload={'messages':[{'role':'user','content':'Explain only.'}],'max_tokens':16}
        with self.keys(), patch.object(self.client,'request',return_value=wallet_limits()) as request:
            result=self.client.run('chat','complete',payload,self.state,self.output,100000,5)
        self.assertEqual(result['status'],'blocked')
        self.assertTrue(all(call.args[0]=='GET' for call in request.call_args_list))
        self.assertFalse(self.state.exists())

    def test_tts_cost_is_characters_and_response_is_binary(self):
        def transport(method,url,key,body=None,**kwargs):
            if url.endswith('/limits'): return wallet_limits()
            if 'wallet-prices' in url: return {'prices':[{'model':'espeech','billing':'chars','unit_rub':.0004,'rub_per_1k_chars':.4}]}
            self.assertEqual(method,'POST')
            self.assertTrue(kwargs['raw'])
            self.assertEqual(json.loads(body)['language'],'Russian')
            return wav_bytes(),'audio/wav'
        with self.keys(), patch.object(self.client,'request',side_effect=transport):
            result=self.client.run('tts','speak',{'input':'Привет'},self.state,self.output,.01,120)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['quota_observed']['units'],6)
        self.assertEqual(result['quota_observed']['estimate_rub'],.01)

    def test_mismatched_tts_price_is_blocked_before_post(self):
        plan=self.client.plan('tts','speak',{'input':'Hello','language':'English'})
        bad={'prices':[{'model':'qwen3-tts','billing':'chars','unit_rub':.0004,'rub_per_1k_chars':4}]}
        with self.assertRaises(self.client.Blocked): self.client.wallet_quote('tts','speak',wallet_limits(),bad,5,plan)

    def test_book_read_only_run_has_no_limits_or_post(self):
        def transport(method,url,key,**kwargs):
            self.assertEqual(method,'GET')
            self.assertEqual(url,'https://neuraldeep.ru/api/v1/books')
            return b'{"books":[]}', 'application/json'
        with self.keys(), patch.object(self.client,'request',side_effect=transport) as request:
            result=self.client.run('books','list',{},self.state,Path(self.tmp.name)/'books.json',None,5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(request.call_count,1)
        self.assertIsNone(result['quota_observed'])

    def test_payload_output_alias_is_rejected(self):
        self.output.write_text('{}',encoding='utf-8')
        with self.assertRaises(self.client.Blocked): self.client.check_paths(self.output,self.state,self.output,'run')

    def test_runtime_under_collection_checkout_is_rejected(self):
        root=Path(self.tmp.name)/'collection'
        (root/'.git').mkdir(parents=True)
        scripts=root/'neuraldeep-chat'/'scripts'
        scripts.mkdir(parents=True)
        with patch.object(self.client,'HERE',scripts):
            with self.assertRaises(self.client.Blocked):
                self.client.check_paths(None,root/'state.json',root/'out.wav','run')


def stat_mode(path):
    return path.stat().st_mode & 0o777


if __name__=='__main__': unittest.main()
