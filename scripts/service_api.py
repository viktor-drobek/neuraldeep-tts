"""Pure request planning and response validation for the new Hub skill family.

This module never resolves credentials, performs I/O, or authorizes spending.
Provider reference: https://neuraldeep.ru/llms-full.txt, read 2026-10-25.
"""
import base64
import binascii
import copy
import io
import ipaddress
import json
import math
import re
from urllib.parse import quote, urlencode, urlsplit
import wave

HUB = 'https://api.neuraldeep.ru/v1'
BOOKS = 'https://neuraldeep.ru/api/v1/books'
SERVICES = {
    'chat': ('complete',), 'embeddings': ('embed',), 'rerank': ('rerank',),
    'tts': ('speak',), 'music': ('generate',), 'pii': ('anonymize', 'deanonymize'),
    'decisions': ('decide',), 'books': ('list', 'toc', 'chapter', 'search'),
    'drift': ('chat',),
}
EMBEDDING_MODELS = {'e5-large', 'bge-m3', 'frida', 'jina-embeddings-v4',
                    'qwen3-embedding-4b', 'giga-embeddings', 'qwen3-embedding-8b'}
RERANK_MODELS = {'bge-reranker', 'qwen3-reranker-8b'}
VOICES = {'aiden', 'dylan', 'ono_anna', 'ryan', 'serena', 'sohee', 'uncle_fu', 'vivian'}
LANGUAGES = {'Auto', 'Chinese', 'English', 'Japanese', 'Korean', 'German', 'French',
             'Russian', 'Portuguese', 'Spanish', 'Italian'}
ENTITIES = {'PERSON', 'LOCATION', 'EMAIL_ADDRESS', 'PHONE_NUMBER', 'URL', 'IP_ADDRESS',
            'IP_PORT', 'DATE_TIME', 'PASSPORT', 'CREDIT_CARD', 'CVC', 'DRIVER_LICENSE',
            'INN', 'KPP', 'OGRN', 'OGRNIP', 'SNILS', 'MILITARY_ID', 'BIRTH_CERTIFICATE',
            'OMS', 'BANK_ACCOUNT', 'BIK', 'POSTAL_CODE', 'TELEGRAM', 'TOKEN'}


def require(condition, message='Invalid request shape'):
    if not condition:
        raise ValueError(message)


def text(value, maximum=None):
    require(isinstance(value, str) and bool(value.strip()), 'Nonempty text required')
    require(maximum is None or len(value) <= maximum, 'Text exceeds the supported character limit')
    return value


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, 'Integer outside the supported range')
    return value


def number(value, low, high):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            'Finite number outside the supported range')
    return value


def fields(body, allowed):
    require(isinstance(body, dict) and set(body) <= set(allowed), 'Unsupported request fields')


def strict_json(data):
    def constant(_):
        raise ValueError('Non-finite JSON number')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON member')
            result[key] = value
        return result
    try:
        return json.loads(data, parse_constant=constant, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('Invalid strict JSON') from None


def _image(url):
    text(url)
    if url.startswith('data:image/'):
        head, separator, encoded = url.partition(',')
        require(bool(separator) and head in {'data:image/png;base64', 'data:image/jpeg;base64',
                'data:image/webp;base64', 'data:image/gif;base64'}, 'Unsupported image data URI')
        try:
            require(bool(base64.b64decode(encoded, validate=True)), 'Empty image data')
        except (ValueError, binascii.Error):
            raise ValueError('Invalid image base64') from None
        return
    try:
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
                and not parsed.password and parsed.port in (None, 443), 'Public HTTPS image URL required')
        host = parsed.hostname.lower()
        require(host != 'localhost' and not host.endswith(('.localhost', '.local', '.internal')),
                'Private image URL is not allowed')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            require('.' in host, 'Public image hostname required')
        else:
            require(address.is_global, 'Private image URL is not allowed')
    except ValueError:
        raise ValueError('Public HTTPS image URL required') from None


def _chat(body):
    fields(body, {'model', 'messages', 'max_tokens', 'stream', 'temperature', 'top_p',
                  'tools', 'tool_choice', 'response_format', 'reasoning_effort', 'stop', 'seed'})
    body.setdefault('model', 'qwen3.6-35b-a3b')
    require(isinstance(body['model'], str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', body['model']),
            'Invalid model identifier')
    require(body.get('stream', False) is False, 'Streaming is not supported by this artifact helper')
    require(type(body.get('max_tokens')) is int and body['max_tokens'] > 0, 'Positive max_tokens required')
    messages = body.get('messages')
    require(isinstance(messages, list) and 1 <= len(messages) <= 512, 'Nonempty messages required')
    images = 0
    for message in messages:
        fields(message, {'role', 'content', 'name', 'tool_calls', 'tool_call_id'})
        require(message.get('role') in {'system', 'developer', 'user', 'assistant', 'tool'}, 'Invalid message role')
        content = message.get('content')
        if isinstance(content, list):
            require(bool(content), 'Nonempty content parts required')
            for part in content:
                require(isinstance(part, dict), 'Content part must be an object')
                if part.get('type') == 'text':
                    fields(part, {'type', 'text'})
                    text(part.get('text'))
                elif part.get('type') == 'image_url':
                    fields(part, {'type', 'image_url'})
                    fields(part.get('image_url'), {'url', 'detail'})
                    _image(part['image_url'].get('url'))
                    require(part['image_url'].get('detail', 'auto') in {'auto', 'low', 'high'}, 'Invalid image detail')
                    images += 1
                else:
                    raise ValueError('Unsupported content part')
        elif content is None:
            require(message.get('role') == 'assistant' and bool(message.get('tool_calls')), 'Message content required')
        else:
            text(content)
        if message['role'] == 'tool':
            text(message.get('tool_call_id'), 256)
        if 'tool_calls' in message:
            calls = message['tool_calls']
            require(message['role'] == 'assistant' and isinstance(calls, list) and 1 <= len(calls) <= 32,
                    'Invalid assistant tool calls')
            for call in calls:
                fields(call, {'id', 'type', 'function'})
                text(call.get('id'), 256)
                require(call.get('type') == 'function', 'Only function tool calls are supported')
                fields(call.get('function'), {'name', 'arguments'})
                text(call['function'].get('name'), 128)
                require(isinstance(call['function'].get('arguments'), str), 'Tool arguments must be a string')
    require(images <= 1, 'Only one image per request is documented')
    for name, low, high in [('temperature', 0, 2), ('top_p', 0, 1)]:
        if name in body:
            number(body[name], low, high)
    if 'seed' in body:
        integer(body['seed'], 0, 2**32-1)
    if 'reasoning_effort' in body:
        require(body['reasoning_effort'] in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}, 'Unsupported reasoning effort')
    if 'stop' in body:
        stops = body['stop'] if isinstance(body['stop'], list) else [body['stop']]
        require(1 <= len(stops) <= 4, 'Invalid stop sequences')
        for value in stops:
            text(value, 256)
    if 'tools' in body:
        require(isinstance(body['tools'], list) and len(body['tools']) <= 128, 'Invalid tools list')
        for tool in body['tools']:
            fields(tool, {'type', 'function'})
            require(tool.get('type') == 'function', 'Only function tools are supported')
            function = tool.get('function')
            fields(function, {'name', 'description', 'parameters', 'strict'})
            require(isinstance(function.get('name'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', function['name']), 'Invalid tool name')
            require(isinstance(function.get('parameters', {}), dict), 'Tool parameters must be a schema object')
            if 'strict' in function:
                require(type(function['strict']) is bool, 'strict must be boolean')
    if 'tool_choice' in body:
        choice = body['tool_choice']
        if isinstance(choice, str):
            require(choice in {'auto', 'none', 'required'}, 'Invalid tool choice')
        else:
            fields(choice, {'type', 'function'})
            require(choice.get('type') == 'function', 'Invalid tool choice')
            fields(choice.get('function'), {'name'})
            text(choice['function'].get('name'), 64)
    if 'response_format' in body:
        fmt = body['response_format']
        fields(fmt, {'type', 'json_schema'})
        require(fmt.get('type') in {'text', 'json_object', 'json_schema'}, 'Unsupported response format')
        if fmt['type'] == 'json_schema':
            schema = fmt.get('json_schema')
            fields(schema, {'name', 'strict', 'schema', 'description'})
            text(schema.get('name'), 64)
            require(schema.get('strict') is True and isinstance(schema.get('schema'), dict), 'Strict JSON schema required')


def _vectors(service, body):
    if service == 'embeddings':
        fields(body, {'model', 'input', 'encoding_format'})
        body.setdefault('model', 'bge-m3')
        require(body['model'] in EMBEDDING_MODELS, 'Unsupported embeddings model')
        require(body.get('encoding_format', 'float') == 'float', 'Only float embeddings are supported')
        items = body.get('input')
        items = items if isinstance(items, list) else [items]
        require(bool(items), 'Embeddings batch must not be empty')
        for item in items:
            text(item, 105600)
        require(sum(map(len, items)) <= 105600, 'Aggregate embedding character limit exceeded')
    else:
        fields(body, {'model', 'query', 'documents', 'top_n', 'return_documents'})
        body.setdefault('model', 'bge-reranker')
        require(body['model'] in RERANK_MODELS, 'Unsupported reranker model')
        query = text(body.get('query'), 105600)
        items = body.get('documents')
        require(isinstance(items, list) and 1 <= len(items) <= 512, 'Rerank requires 1 to 512 documents')
        for item in items:
            text(item, 105600)
        require(len(query) + sum(map(len, items)) <= 105600, 'Aggregate rerank character limit exceeded')
        if 'top_n' in body:
            integer(body['top_n'], 1, len(items))
        if 'return_documents' in body:
            require(type(body['return_documents']) is bool, 'return_documents must be boolean')


def _pii(operation, body):
    fields(body, {'text', 'texts', 'entities', 'mapping'})
    require(('text' in body) != ('texts' in body), 'Exactly one of text or texts is required')
    items = body.get('texts') if 'texts' in body else [body['text']]
    require(isinstance(items, list) and 1 <= len(items) <= 512, 'PII batch must contain 1 to 512 texts')
    for item in items:
        text(item, 1000000)
    if operation == 'anonymize':
        require('mapping' not in body, 'Anonymize does not accept a restoration mapping')
        if 'entities' in body:
            entities = body['entities']
            require(isinstance(entities, list) and 1 <= len(entities) <= len(ENTITIES)
                    and all(isinstance(item, str) and item in ENTITIES for item in entities)
                    and len(set(entities)) == len(entities), 'Unsupported PII entities')
    else:
        require('entities' not in body, 'Restoration does not accept entities')
        mapping = body.get('mapping')
        require(isinstance(mapping, dict) and all(isinstance(k, str) and bool(k) and isinstance(v, str)
                                                 for k, v in mapping.items()), 'Restoration mapping required')


def _decisions(body):
    fields(body, {'model', 'state', 'questions'})
    body.setdefault('model', 'frida-decisions')
    require(body['model'] in {'frida-decisions', 'clef-flash'}, 'Unsupported decisions model')
    require('state' in body, 'Decision state required')
    questions = body.get('questions')
    require(isinstance(questions, dict) and 1 <= len(questions) <= 32, 'Decisions requires 1 to 32 questions')
    options = 0
    for name, question in questions.items():
        require(isinstance(name, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name)), 'Invalid question identifier')
        fields(question, {'type', 'instructions', 'criteria'})
        kind = question.get('type')
        require(kind in {'choice', 'score', 'noul'}, 'Ranking answer schema is unverified; request is blocked')
        text(question.get('instructions'))
        criteria = question.get('criteria')
        if kind == 'noul':
            if criteria is not None:
                require(isinstance(criteria, dict) and set(criteria) == {'true', 'false'}, 'noul criteria must be true and false')
                for value in criteria.values():
                    text(value)
            options += 2
        elif kind == 'choice':
            require(isinstance(criteria, dict) and 2 <= len(criteria) <= 512, 'Choice requires at least two named options')
            for key, value in criteria.items():
                text(key, 128)
                text(value)
            options += len(criteria)
        else:
            require(isinstance(criteria, list) and 2 <= len(criteria) <= 512, 'Score requires an ordered criteria list')
            for value in criteria:
                text(value)
            options += len(criteria)
    require(options <= 512, 'Total decision options exceed 512')
    require(len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')) <= 256 * 1024, 'Decision body exceeds 256 KiB')
    return math.ceil(options / 16) * (4 if body['model'] == 'clef-flash' else 1)


def _books(operation, body):
    if operation == 'list':
        fields(body, set())
        return BOOKS
    allowed = {'slug'}
    if operation == 'chapter':
        allowed.add('chapter')
    elif operation == 'search':
        allowed |= {'q', 'limit', 'mode'}
    fields(body, allowed)
    slug = body.get('slug', 'agenty-vajbkoding')
    require(isinstance(slug, str) and bool(re.fullmatch(r'[a-z0-9-]{1,64}', slug)), 'Invalid book slug')
    prefix = BOOKS + '/' + quote(slug, safe='')
    if operation == 'toc':
        return prefix + '/toc'
    if operation == 'chapter':
        chapter = body.get('chapter')
        require(isinstance(chapter, str) and bool(re.fullmatch(r'[A-Za-z0-9А-Яа-яЁё_-]{1,64}', chapter)), 'Invalid chapter identifier')
        return prefix + '/chapter/' + quote(chapter, safe='')
    text(body.get('q'), 2000)
    require(body.get('mode', 'fts') == 'fts', 'Only non-inference fts search is supported')
    limit = integer(body.get('limit', 10), 1, 20)
    return prefix + '/search?' + urlencode({'q': body['q'], 'mode': 'fts', 'limit': limit})


def plan(service, operation, payload):
    require(service in SERVICES and operation in SERVICES[service], 'Unsupported service or operation')
    require(isinstance(payload, dict), 'Payload must be an object')
    try:
        json.dumps(payload, allow_nan=False)
        body = copy.deepcopy(payload)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('Payload must contain finite JSON values') from None
    result = {'method': 'POST', 'body': body, 'price_id': None, 'units': 0, 'kind': 'token',
              'async': False, 'quota_path': None, 'subscription_supported': False}
    if service in {'chat', 'drift'}:
        _chat(body)
        result['url'] = HUB + '/chat/completions' if service == 'chat' else 'https://drift.neuraldeep.ru/v1/chat/completions'
    elif service in {'embeddings', 'rerank'}:
        _vectors(service, body)
        result['url'] = HUB + '/' + service
    elif service == 'tts':
        fields(body, {'input', 'voice', 'language', 'instructions'})
        text(body.get('input'), 5000)
        body.setdefault('voice', 'serena')
        body.setdefault('language', 'Russian')
        require(body['voice'] in VOICES and body['language'] in LANGUAGES, 'Unsupported voice or language')
        if 'instructions' in body:
            text(body['instructions'], 2000)
        result.update(url=HUB + '/audio/speech', price_id='espeech' if body['language'] == 'Russian' else 'qwen3-tts',
                      units=len(body['input']), kind='chars')
    elif service == 'music':
        fields(body, {'prompt', 'lyrics', 'vocal_language', 'audio_duration', 'thinking', 'batch_size', 'audio_format', 'seed'})
        require(bool(body.get('prompt')) or bool(body.get('lyrics')), 'Music requires prompt or lyrics')
        for key in ('prompt', 'lyrics'):
            if key in body:
                text(body[key], 10000)
        body.setdefault('batch_size', 1)
        body.setdefault('audio_format', 'wav')
        body.setdefault('audio_duration', 120)
        integer(body['batch_size'], 1, 2)
        number(body['audio_duration'], 10, 300)
        require(body['audio_format'] == 'wav', 'Only WAV output is supported')
        if 'thinking' in body:
            require(type(body['thinking']) is bool, 'thinking must be boolean')
        if 'vocal_language' in body:
            text(body['vocal_language'], 32)
        if 'seed' in body:
            integer(body['seed'], 0, 2**32-1)
        result.update(url=HUB + '/music/generate', price_id='music:generate', units=body['batch_size'],
                      kind='unit', quota_path='/music/quota')
        result['async'] = True
    elif service == 'pii':
        _pii(operation, body)
        result.update(url=HUB + '/pii/' + operation, quota_path='/pii/quota')
    elif service == 'decisions':
        units = _decisions(body)
        result.update(url=HUB + '/decisions', price_id='text:decisions', units=units, kind='unit', quota_path='/decisions/quota')
    else:
        result.update(method='GET', url=_books(operation, body), body=None, kind='free')
    return result


def _wav(data, media_type, service):
    require(media_type.split(';', 1)[0].strip().lower() in {'audio/wav', 'audio/x-wav', 'audio/wave'}, 'WAV media type required')
    require(len(data) >= 44 and data[:4] == b'RIFF' and data[8:12] == b'WAVE', 'Invalid WAV container')
    require(int.from_bytes(data[4:8], 'little') + 8 == len(data), 'Truncated or overlong WAV container')
    try:
        with wave.open(io.BytesIO(data), 'rb') as stream:
            require(stream.getnframes() > 0 and stream.getnchannels() in (1, 2), 'Empty or unsupported WAV')
            if service == 'tts':
                require(stream.getframerate() == 24000 and stream.getsampwidth() == 2 and stream.getnchannels() == 1,
                        'TTS must be mono 24 kHz 16-bit PCM')
            expected = stream.getnframes() * stream.getnchannels() * stream.getsampwidth()
            require(len(stream.readframes(stream.getnframes())) == expected, 'Truncated WAV audio data')
    except (wave.Error, EOFError, OverflowError):
        raise ValueError('Unsupported WAV container') from None


def _probabilities(value, expected=None):
    require(isinstance(value, dict) and bool(value), 'Probabilities required')
    if expected is not None:
        require(set(value) == set(expected), 'Probability options do not match the request')
    for probability in value.values():
        number(probability, 0, 1)


def validate_result(service, operation, payload, data, media_type):
    require(service in SERVICES and operation in SERVICES[service], 'Unsupported result operation')
    require(isinstance(data, bytes) and bool(data), 'Empty result body')
    if service in {'music', 'tts'}:
        _wav(data, media_type, service)
        return data
    require(media_type.split(';', 1)[0].strip().lower() == 'application/json', 'JSON media type required')
    value = strict_json(data)
    require(isinstance(value, dict) and 'error' not in value and 'detail' not in value
            and value.get('success') is not False, 'Provider error is not a successful result')
    if service in {'chat', 'drift'}:
        choices = value.get('choices')
        require(isinstance(choices, list) and bool(choices), 'Chat choices required')
        for choice in choices:
            require(isinstance(choice, dict) and isinstance(choice.get('message'), dict), 'Invalid chat choice')
            message = choice['message']
            require(message.get('role') == 'assistant' and (isinstance(message.get('content'), str)
                    or isinstance(message.get('tool_calls'), list) and bool(message['tool_calls'])), 'Invalid assistant message')
    elif service == 'embeddings':
        inputs = payload.get('input')
        count = len(inputs) if isinstance(inputs, list) else 1
        vectors = value.get('data')
        require(isinstance(vectors, list) and len(vectors) == count, 'Embedding count does not match input')
        indices, dimensions = [], []
        for item in vectors:
            require(isinstance(item, dict), 'Invalid embedding item')
            integer(item.get('index'), 0, count-1)
            vector = item.get('embedding')
            require(isinstance(vector, list) and bool(vector), 'Embedding vector required')
            for scalar in vector:
                require(type(scalar) in (int, float) and math.isfinite(scalar), 'Embedding must contain finite numbers')
            indices.append(item['index'])
            dimensions.append(len(vector))
        require(set(indices) == set(range(count)) and len(set(dimensions)) == 1, 'Invalid vector indices or dimensions')
    elif service == 'rerank':
        items = value.get('results')
        docs = payload['documents']
        require(isinstance(items, list) and len(items) == payload.get('top_n', len(docs)), 'Rerank result count mismatch')
        indices = []
        for item in items:
            require(isinstance(item, dict), 'Invalid rerank result')
            integer(item.get('index'), 0, len(docs)-1)
            require(type(item.get('relevance_score')) in (int, float) and math.isfinite(item['relevance_score']), 'Finite relevance score required')
            indices.append(item['index'])
        require(len(indices) == len(set(indices)), 'Duplicate rerank indices')
    elif service == 'pii':
        key = 'anonymized' if operation == 'anonymize' else 'deanonymized'
        if 'texts' in payload:
            require(isinstance(value.get(key), list) and len(value[key]) == len(payload['texts'])
                    and all(isinstance(item, str) for item in value[key]), 'PII batch result shape mismatch')
        else:
            require(isinstance(value.get(key), str), 'PII text result required')
        if operation == 'anonymize':
            mapping = value.get('mapping')
            require(value.get('action') in {'ANONYMIZE', 'NONE'} and isinstance(mapping, dict)
                    and all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()), 'Invalid PII mapping')
    elif service == 'decisions':
        prepared = plan(service, operation, payload)
        questions = prepared['body']['questions']
        answers = value.get('answers')
        require(isinstance(answers, dict) and set(answers) == set(questions), 'Decision answers do not match questions')
        for key, question in questions.items():
            answer = answers[key]
            kind = question['type']
            require(isinstance(answer, dict) and answer.get('type') == kind, 'Decision answer type mismatch')
            if kind == 'choice':
                require(answer.get('choice') in question['criteria'], 'Unknown decision choice')
                number(answer.get('confidence'), 0, 1)
                _probabilities(answer.get('probabilities'), question['criteria'])
            elif kind == 'score':
                number(answer.get('score'), 0, 1)
                _probabilities(answer.get('probabilities'), [str(i) for i in range(len(question['criteria']))])
            else:
                number(answer.get('noul'), 0, 1)
        usage = value.get('usage')
        require(isinstance(usage, dict), 'Decision usage required')
        integer(usage.get('questions'), len(questions), len(questions))
        integer(usage.get('units_charged'), 1, prepared['units'])
        integer(usage.get('options'), 1, 512)
    else:
        if operation == 'list':
            require(isinstance(value.get('books'), list), 'Book catalogue required')
        elif operation == 'toc':
            require(isinstance(value.get('chapters'), list), 'Chapter list required')
        elif operation == 'search':
            require(isinstance(value.get('hits'), list), 'Search hits required')
        else:
            require(any(isinstance(value.get(key), str) for key in ('text', 'content', 'markdown')), 'Chapter text required')
    return data
