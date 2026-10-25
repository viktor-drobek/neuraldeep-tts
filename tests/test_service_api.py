"""Pure API contract tests; no credentials, network or runtime state."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
import wave

SPEC = importlib.util.spec_from_file_location('service_api_test', Path(__file__).parents[1] / 'scripts/service_api.py')
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class PlanTests(unittest.TestCase):
    def test_nine_services(self):
        self.assertEqual(set(api.SERVICES), {'chat','embeddings','rerank','tts','music','pii','decisions','books','drift'})

    def test_music_cost_and_format(self):
        plan = api.plan('music','generate',{'prompt':'Piano', 'batch_size':2})
        self.assertEqual(plan['units'],2)
        self.assertEqual(plan['price_id'],'music:generate')
        self.assertEqual(plan['body']['audio_format'],'wav')
        self.assertTrue(plan['async'])
        self.assertFalse(plan['subscription_supported'])
        for bad in ({'prompt':'Piano','batch_size':True},{'prompt':'Piano','audio_duration':301},
                    {'prompt':'Piano','audio_format':'mp3'},{'prompt':'Piano','task_type':'cover'},{}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                api.plan('music','generate',bad)

    def test_tts_counts_characters_not_requests(self):
        p = api.plan('tts','speak',{'input':'Привет'})
        self.assertEqual(p['units'],6)
        self.assertEqual(p['price_id'],'espeech')
        self.assertEqual(api.plan('tts','speak',{'input':'Hello','language':'English'})['price_id'],'qwen3-tts')
        for bad in ({'input':'x'*5001},{'input':''},{'input':'x','voice':'alloy'}):
            with self.assertRaises(ValueError): api.plan('tts','speak',bad)

    def test_decisions_option_units(self):
        questions={'q':{'type':'choice','instructions':'Choose','criteria':{str(i):str(i) for i in range(17)}}}
        self.assertEqual(api.plan('decisions','decide',{'state':'x','questions':questions})['units'],2)
        p=api.plan('decisions','decide',{'model':'clef-flash','state':'x','questions':questions})
        self.assertEqual(p['units'],8)
        self.assertEqual(p['price_id'],'text:decisions')
        self.assertEqual(api.plan('decisions','decide',{'state':'x','questions':{'q':{'type':'noul','instructions':'Is it?'}}})['units'],1)

    def test_unknown_decision_shape_is_not_billed(self):
        for model in ('frida-decisions','clef-flash'):
            with self.assertRaises(ValueError):
                api.plan('decisions','decide',{'model':model,'state':'x','questions':{'q':{'type':'ranking','instructions':'Rank','criteria':['a','b']}}})

    def test_vector_limits(self):
        self.assertEqual(api.plan('embeddings','embed',{'input':['a','b']})['body']['model'],'bge-m3')
        for bad in ({'input':[]},{'input':[True]},{'input':'x'*105601}):
            with self.assertRaises(ValueError): api.plan('embeddings','embed',bad)
        with self.assertRaises(ValueError): api.plan('rerank','rerank',{'query':'x','documents':['y'*105600]})

    def test_chat_is_nonstreaming_and_bounded(self):
        p={'messages':[{'role':'user','content':'Hello'}],'max_tokens':32}
        self.assertEqual(api.plan('chat','complete',p)['body']['model'],'qwen3.6-35b-a3b')
        for extra in ({'stream':True},{'max_tokens':True},{'max_tokens':0},{'extra_body':{}},{'temperature':float('nan')}):
            with self.assertRaises(ValueError): api.plan('chat','complete',dict(p,**extra))
        with self.assertRaises(ValueError): api.plan('chat','complete',{'messages':[],'max_tokens':32})

    def test_vision_rejects_private_urls_and_multiple_images(self):
        def payload(urls):
            return {'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':u}} for u in urls]}],'max_tokens':32}
        for urls in (['https://127.0.0.1/a.png'],['https://example.com/a.png','https://example.com/b.png'],['http://example.com/a.png']):
            with self.assertRaises(ValueError): api.plan('chat','complete',payload(urls))
        api.plan('chat','complete',payload(['https://example.com/a.png']))

    def test_pii_batches_and_mapping(self):
        api.plan('pii','anonymize',{'texts':['One','Two']})
        for bad in ({'text':'a','texts':['b']},{'texts':['x']*513},{'text':'x','entities':['unknown']}):
            with self.assertRaises(ValueError): api.plan('pii','anonymize',bad)
        with self.assertRaises(ValueError): api.plan('pii','deanonymize',{'text':'x'})

    def test_books_use_fixed_origin_fts_and_encoded_query(self):
        p=api.plan('books','search',{'q':'tools & context'})
        self.assertEqual(p['method'],'GET')
        self.assertIn('mode=fts',p['url'])
        self.assertIn('q=tools+%26+context',p['url'])
        self.assertIsNone(p['price_id'])
        for bad in ({'q':'x','mode':'hybrid'},{'q':'x','slug':'../other'}):
            with self.assertRaises(ValueError): api.plan('books','search',bad)

    def test_errors_do_not_echo_user_content(self):
        with self.assertRaises(ValueError) as error:
            api.plan('music','generate',{'private-name-secret':'sensitive'})
        self.assertNotIn('private-name-secret',str(error.exception))
        self.assertNotIn('sensitive',str(error.exception))


class ResultTests(unittest.TestCase):
    def validate(self,service,operation,payload,value):
        return api.validate_result(service,operation,payload,json.dumps(value).encode(),'application/json')

    def test_embeddings_validate_count_indices_and_finite_vectors(self):
        self.validate('embeddings','embed',{'input':['a','b']},{'data':[{'index':1,'embedding':[1.,2.]},{'index':0,'embedding':[3.,4.]}]})
        for values in ([{'index':0,'embedding':[1.]},{'index':0,'embedding':[2.]}],
                       [{'index':0,'embedding':[float('nan')]}], []):
            with self.assertRaises(ValueError): self.validate('embeddings','embed',{'input':['a','b']},{'data':values})

    def test_rerank_checks_indices_and_scores(self):
        self.validate('rerank','rerank',{'query':'x','documents':['a']},{'results':[{'index':0,'relevance_score':.8}]})
        with self.assertRaises(ValueError): self.validate('rerank','rerank',{'query':'x','documents':['a']},{'results':[{'index':3,'relevance_score':.8}]})

    def test_pii_preserves_batch_shape(self):
        self.validate('pii','anonymize',{'texts':['a']},{'anonymized':['a'],'mapping':{},'action':'NONE'})
        with self.assertRaises(ValueError): self.validate('pii','anonymize',{'texts':['a']},{'anonymized':'a','mapping':{},'action':'NONE'})

    def test_decision_must_answer_the_requested_question(self):
        payload={'state':'x','questions':{'q':{'type':'noul','instructions':'Yes?'}}}
        self.validate('decisions','decide',payload,{'answers':{'q':{'type':'noul','noul':.5}},'usage':{'options':2,'questions':1,'units_charged':1}})
        with self.assertRaises(ValueError): self.validate('decisions','decide',payload,{'answers':{},'usage':{}})

    def test_wav_requires_a_complete_supported_container(self):
        with self.assertRaises(ValueError): api.validate_result('tts','speak',{'input':'x'},b'RIFF'+b'\x00'*100,'audio/wav')
        output=io.BytesIO()
        with wave.open(output,'wb') as stream:
            stream.setparams((1,2,24000,0,'NONE','not compressed'))
            stream.writeframes(b'\0\0'*120)
        data=output.getvalue()
        self.assertEqual(api.validate_result('tts','speak',{'input':'x'},data,'audio/wav'),data)
        with self.assertRaises(ValueError): api.validate_result('tts','speak',{'input':'x'},data[:-8],'audio/wav')

    def test_error_objects_are_never_success(self):
        for value in ({'detail':'private'},{'error':{}},{'success':False}):
            with self.assertRaises(ValueError): self.validate('books','list',{},value)


if __name__=='__main__': unittest.main()
