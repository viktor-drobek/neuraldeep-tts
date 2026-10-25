#!/usr/bin/env python3
"""Guarded, dependency-free CLI for the nine new NeuralDeep API skills.

Check is read-only. Run submits once after validation and explicit wallet
approval. Resume only retrieves an existing music job. New subscription
submissions, token-billed inference and Drift execution remain blocked.
"""
import argparse
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guarded as base

SERVICE = "tts"
KEY_VARIABLES = {name: 'NEURALDEEP_' + name.upper() + '_API_KEY' for name in
                 ('chat', 'embeddings', 'rerank', 'tts', 'music', 'pii', 'decisions', 'books')}
KEY_VARIABLES['drift'] = 'NEURALDEEP_DRIFT_API_TOKEN'
SHARED_KEY = 'NEURALDEEP_API_KEY'
HUB = 'https://api.neuraldeep.ru/v1'
PRICES = 'https://neuraldeep.ru/api/public/wallet-prices'
OPERATIONS = {'chat': ('complete',), 'embeddings': ('embed',), 'rerank': ('rerank',),
              'tts': ('speak',), 'music': ('generate',), 'pii': ('anonymize', 'deanonymize'),
              'decisions': ('decide',), 'books': ('list', 'toc', 'chapter', 'search'), 'drift': ('chat',)}
WALLET_OPERATIONS = {'tts': 'speak', 'music': 'generate', 'decisions': 'decide'}
FAILED_STATUSES = {'failed', 'cancelled', 'canceled', 'error', 'expired', 'rejected'}
REQUEST_TIMEOUT = 30
DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 2
MAX_TTS_CHARS = 5000
Blocked, WalletApproval = base.Blocked, base.WalletApproval
RequestError, JobError, PollTimeout = base.RequestError, base.JobError, base.PollTimeout
_LOCAL_ERRORS = (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError)
_SERVICE_API = None


def load_service_api():
    global _SERVICE_API
    if _SERVICE_API is None:
        path = HERE / 'service_api.py'
        if not path.is_file():
            return None
        spec = importlib.util.spec_from_file_location('nd_service_api', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SERVICE_API = module
    return _SERVICE_API


def _validator():
    api = load_service_api()
    if api is None:
        raise ValueError('Service contract module unavailable; execution blocked')
    return api


def plan(service, operation, payload):
    return _validator().plan(service, operation, payload)


def validate_result(service, operation, payload, data, media_type):
    return _validator().validate_result(service, operation, payload, data, media_type)


def resolve_keys(service=None):
    """Only this helper resolves credentials; values never enter state or output."""
    service = service or SERVICE
    own = KEY_VARIABLES[service]
    names = (own,) if service == 'drift' else (own, SHARED_KEY)
    dotenv = None
    for name in names:
        keys = base.usable_keys(os.environ.get(name))
        if not keys:
            if dotenv is None:
                try:
                    home = Path(os.environ.get('CODDY_HOME') or Path.home() / '.coddy').expanduser()
                except (RuntimeError, KeyError):
                    dotenv = {}
                else:
                    dotenv = base.read_dotenv(home)
            keys = base.usable_keys(dotenv.get(name))
        if keys:
            return name, keys
    raise Blocked('No configured credential; add ' + own +
                  (' or ' + SHARED_KEY if service != 'drift' else '') +
                  ' to the private environment, never to chat or the repository')


def request(method, url, key, body=None, content_type='application/json', *, timeout=REQUEST_TIMEOUT, raw=False):
    data, media = base.request(method, url, key, body, content_type, timeout=timeout, raw=True)
    if raw:
        return data, media
    if media != 'application/json':
        raise RequestError('API response is not JSON; no automatic retry')
    try:
        return _validator().strict_json(data)
    except ValueError:
        raise RequestError('API response is not strict JSON; no automatic retry') from None


def subscription_guard(service, operation, limits, prices, quota, units, plan_):
    raise Blocked('Authenticated service quota schema unverified; all new subscription submissions are blocked')


def _row_rate(row, service):
    if service == 'tts':
        unit, per_1k = row.get('unit_rub'), row.get('rub_per_1k_chars')
        if (row.get('billing') != 'chars' or not base.finite(unit) or not base.finite(per_1k)
                or unit < 0 or per_1k < 0):
            raise Blocked('TTS price row has no verified character billing')
        if Decimal(str(unit)) * 1000 != Decimal(str(per_1k)):
            raise Blocked('TTS character price fields disagree')
        return unit
    if row.get('billing') != 'unit':
        raise Blocked('Operation requires an explicit per-unit public price')
    return row.get('unit_rub')


def wallet_quote(service, operation, limits, prices, units, plan_, wallet_limit=None):
    if WALLET_OPERATIONS.get(service) != operation:
        raise Blocked('Token spend bound unverified; wallet approval cannot enable this operation')
    try:
        if limits['key']['billing_mode'] != 'wallet' or limits['key']['status'] != 'ok':
            raise Blocked('Requires an ok wallet key')
        rows = [row for row in prices['prices'] if isinstance(row, dict) and row.get('model') == plan_['price_id']]
        if len(rows) != 1:
            raise Blocked('Operation has no single public price row')
        rate = _row_rate(rows[0], service)
        if not base.finite(rate) or rate < 0 or type(units) is not int or units <= 0:
            raise Blocked('Unverified billing rate or units')
        estimate_decimal = (Decimal(str(rate)) * units).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
        estimate = float(estimate_decimal)
        if not base.finite(estimate):
            raise Blocked('Invalid wallet estimate')
        balance = limits['wallet']['balance_rub']
        cap = limits['key']['cap']
        spend_cap, spent = cap['spend_cap_rub'], cap['spent_rub']
        if (not all(base.finite(n) and n >= 0 for n in (balance, spend_cap, spent))
                or type(cap['cap_blocked']) is not bool):
            raise Blocked('Unrecognized wallet balance or spending cap')
        if Decimal(str(balance)) < estimate_decimal:
            raise Blocked('Wallet balance is below the estimated cost')
        if cap['cap_blocked'] or (spend_cap > 0 and Decimal(str(spent)) + estimate_decimal > Decimal(str(spend_cap))):
            raise Blocked('The key spending cap does not cover the estimated cost')
    except (KeyError, TypeError, AttributeError, InvalidOperation, OverflowError):
        raise Blocked('Unrecognized limits, public prices or wallet schema') from None
    observed = {'observed_at': base.now(), 'billing': 'wallet', 'price_id': plan_['price_id'],
                'units': units, 'rate_rub': rate, 'estimate_rub': estimate, 'balance_rub': balance}
    if wallet_limit is not None:
        if not base.finite(wallet_limit) or wallet_limit <= 0 or Decimal(str(wallet_limit)) < estimate_decimal:
            raise Blocked('Estimated cost exceeds the approved amount; ask the user again')
        observed['approved_limit_rub'] = wallet_limit
    return observed


def wallet_warning(observed):
    key = observed['key']
    return (f"Key {key['index']} of {key['variable']} pays from the wallet. "
            f"Estimated cost {observed['estimate_rub']:.2f} RUB, "
            f"wallet balance {observed['balance_rub']:.2f} RUB. "
            'Ask the user whether to pay. Only after explicit approval repeat with '
            f"--wallet-limit {observed['estimate_rub']:.2f}")


def preflight(service, operation, key, units, plan_):
    limits = request('GET', HUB + '/limits', key)
    if not isinstance(limits, dict) or not isinstance(limits.get('key'), dict):
        raise Blocked('Unrecognized limits schema')
    if limits['key'].get('billing_mode') != 'wallet':
        subscription_guard(service, operation, limits, {}, {}, units, plan_)
    if WALLET_OPERATIONS.get(service) != operation:
        raise Blocked('Token spend bound unverified; inference remains blocked')
    prices = request('GET', PRICES, key)
    return wallet_quote(service, operation, limits, prices, units, plan_)


def choose_key(variable, keys, service, operation, units, plan_, wallet_limit=None):
    reasons, wallet = [], None
    for index, key in enumerate(keys, 1):
        try:
            observed = preflight(service, operation, key, units, plan_)
        except (Blocked, RequestError) as error:
            reasons.append(str(error))
            continue
        observed['key'] = {'variable': variable, 'index': index}
        wallet = wallet or (key, observed)
    if wallet is None:
        raise Blocked('; '.join(f'key {i}: {reason}' for i, reason in enumerate(reasons, 1)) or 'No usable key')
    key, observed = wallet
    if wallet_limit is None:
        raise WalletApproval(wallet_warning(observed), observed)
    if not base.finite(wallet_limit) or wallet_limit <= 0 or observed['estimate_rub'] > wallet_limit:
        raise Blocked('Estimated cost exceeds the approved amount; ask the user again')
    observed['approved_limit_rub'] = wallet_limit
    return key, observed


def resume_key(state, variable, keys):
    observed = state.get('quota_observed')
    record = observed.get('key') if isinstance(observed, dict) else None
    if isinstance(record, dict) and record.get('variable') == variable:
        index = record.get('index')
        if type(index) is int and 1 <= index <= len(keys):
            return keys[index-1]
    raise Blocked('Restore the original submitting key and its position to resume; never submit again')


def state_template(task_id, service):
    return {'task_id': task_id, 'service': service, 'operation': None, 'provider_job_id': None,
            'status': 'blocked', 'artifacts': [], 'quota_observed': None, 'timeouts': {},
            'errors': [], 'updated_at': base.now()}


def new_state(path, task_id, service, operation):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Blocked('State already exists; resume or reconcile it, never submit it again') from None
    os.close(fd)
    state = state_template(task_id, service)
    state['operation'] = operation
    base.save_state(path, state)
    return state


def _inside_checkout(path, checkout):
    resolved = path.resolve()
    return resolved == checkout or checkout in resolved.parents


def check_paths(payload_path, state_path, output_path, command):
    checkout = HERE.parent.resolve()
    checkouts = [checkout]
    for parent in (checkout, *checkout.parents):
        if (parent / '.git').exists():
            checkouts.append(parent)
            break
    paths = [('payload', Path(payload_path))] if payload_path is not None else []
    if command in ('run', 'resume'):
        paths += [('state', Path(state_path)), ('output', Path(output_path))]
    for label, path in paths:
        if any(_inside_checkout(path, folder) for folder in checkouts):
            raise Blocked('Runtime files must be outside the skill checkout')
        if os.path.lexists(path):
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise Blocked('Runtime paths must be regular files, never symlinks or special files')
        elif label == 'payload' or label == 'state' and command == 'resume':
            raise Blocked('Required runtime file is missing')
        if label != 'payload':
            parent = path.absolute().parent
            if not parent.is_dir() or not os.access(parent, os.R_OK | os.W_OK | os.X_OK):
                raise Blocked('Runtime directory is missing or not writable')
            if label == 'output' and os.path.lexists(path):
                raise Blocked('Artifact path already exists; choose a new private path')
            if label == 'state' and command == 'run' and os.path.lexists(path):
                raise Blocked('State already exists; resume or reconcile, never resubmit')
    for index, (_, path) in enumerate(paths):
        if any(base.same_file(path, other) for _, other in paths[index+1:]):
            raise Blocked('Payload, state and artifact must be distinct files')


def load_payload(payload_path, operation):
    if payload_path is None:
        if operation == 'list':
            return {}
        raise Blocked('A private JSON payload file is required')
    try:
        data = Path(payload_path).read_bytes()
        payload = _validator().strict_json(data)
    except _LOCAL_ERRORS:
        raise Blocked('Cannot read a strict JSON payload') from None
    if not isinstance(payload, dict):
        raise Blocked('Payload must be a JSON object')
    return payload


def music_urls(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', job_id):
        raise RequestError('Missing or invalid provider job ID; reconcile without resubmitting')
    return HUB + '/music/tasks/' + quote(job_id, safe='')


def poll_music(get, job_id, *, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PollTimeout('Polling deadline reached; resume the same provider job')
        response = get(music_urls(job_id), timeout=min(REQUEST_TIMEOUT, remaining))
        if time.monotonic() >= deadline:
            raise PollTimeout('Polling deadline reached; resume the same provider job')
        status = response.get('status') if isinstance(response, dict) else None
        if not isinstance(status, str):
            raise RequestError('Unrecognized task status; resume the same provider job')
        if status == 'succeeded':
            return response
        if status in FAILED_STATUSES:
            raise JobError('Provider job failed or was cancelled')
        time.sleep(min(interval, max(0, deadline-time.monotonic())))


def music_tracks(response):
    tracks = response.get('tracks')
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 2:
        raise RequestError('Succeeded music task has no verified track list')
    return len(tracks)


def fetch_track(get, job_id, index, tracks, timeout):
    if type(index) is not int or not 0 <= index < tracks:
        raise ValueError('Track index is outside this task batch')
    if timeout <= 0:
        raise PollTimeout('Result deadline reached; resume the same provider job')
    return get(music_urls(job_id) + '/result?index=' + str(index), timeout=min(REQUEST_TIMEOUT, timeout), raw=True)


def _retrieve_music(key, state, output, timeout, index):
    deadline = time.monotonic() + timeout
    get = lambda url, **kw: request('GET', url, key, **kw)
    status = poll_music(get, state['provider_job_id'], timeout=timeout)
    data, media = fetch_track(get, state['provider_job_id'], index, music_tracks(status), deadline-time.monotonic())
    if time.monotonic() >= deadline:
        raise PollTimeout('Result deadline reached; resume the same provider job')
    data = validate_result('music', 'generate', {}, data, media)
    endpoint = music_urls(state['provider_job_id']) + '/result?index=' + str(index)
    state['artifacts'].append(base.save_artifact(output, data, endpoint))
    state['status'] = 'completed'


def _failure(service, operation, error):
    state = state_template('standalone', service)
    state['operation'] = operation
    if isinstance(error, WalletApproval):
        state['status'], state['quota_observed'] = 'wallet_approval_required', error.observed
    state['errors'] = [str(error) if isinstance(error, (Blocked, WalletApproval, RequestError, JobError))
                       else 'Invalid input, private paths or service contract; execution blocked']
    return state


def _finish(path, state):
    state['updated_at'] = base.now()
    try:
        base.save_state(path, state)
    except OSError:
        state['errors'].append('State could not be saved; preserve this envelope and reconcile, never resubmit')
        state['status'] = 'poll_error' if state.get('provider_job_id') else 'reconciliation_required'
    return state


def _record_error(state, error):
    if isinstance(error, PollTimeout):
        state['status'] = 'timed_out'
    elif state['status'] != 'reconciliation_required':
        state['status'] = 'failed' if isinstance(error, JobError) else 'poll_error'
    state['errors'].append(str(error) if isinstance(error, (RequestError, JobError, PollTimeout, Blocked))
                           else 'Local I/O or response schema error; preserve state, never resubmit')


def _drift_block():
    raise Blocked('Drift entitlement and remote-action budget unverified; no remote commands are sent')


def check(service, operation, payload, state_path=None, wallet_limit=None):
    try:
        prepared = plan(service, operation, payload)
        if service == 'drift':
            _drift_block()
        if service == 'books':
            resolve_keys(service)
            return {'status': 'ready', 'billing': 'read_only', 'errors': []}
        variable, keys = resolve_keys(service)
        _, observed = choose_key(variable, keys, service, operation, prepared['units'], prepared, wallet_limit)
        return {'status': 'ready', 'quota_observed': observed, 'errors': []}
    except (Blocked, WalletApproval, RequestError) + _LOCAL_ERRORS as error:
        return _failure(service, operation, error)


def run(service, operation, payload, state_path, output_path, wallet_limit, timeout, index=None):
    state = None
    try:
        prepared = plan(service, operation, payload)
        check_paths(None, state_path, output_path, 'run')
        if not base.finite(timeout) or timeout <= 0:
            raise Blocked('Positive finite timeout required')
        if wallet_limit is not None and (not base.finite(wallet_limit) or wallet_limit <= 0):
            raise Blocked('Positive finite approved wallet limit required')
        if service == 'drift':
            _drift_block()
        index = 0 if index is None else index
        if service == 'music' and (type(index) is not int or not 0 <= index < prepared['body']['batch_size']):
            raise Blocked('Track index is outside the requested batch; no submission made')
        variable, keys = resolve_keys(service)
        key, observed = (keys[0], None) if service == 'books' else choose_key(
            variable, keys, service, operation, prepared['units'], prepared, wallet_limit)
        state = new_state(state_path, 'standalone', service, operation)
        state['quota_observed'] = observed
        state['timeouts'] = {'request_seconds': 120 if service == 'tts' else REQUEST_TIMEOUT, 'poll_seconds': timeout}
        state['status'] = 'retrieving' if service == 'books' else 'reconciliation_required'
        base.save_state(state_path, state)
        if service == 'books':
            data, media = request('GET', prepared['url'], key, timeout=min(timeout, REQUEST_TIMEOUT), raw=True)
            data = validate_result(service, operation, payload, data, media)
            state['artifacts'] = [base.save_artifact(output_path, data, prepared['url'])]
            state['status'] = 'completed'
        else:
            body = json.dumps(prepared['body'], ensure_ascii=False, allow_nan=False).encode('utf-8')
            if service == 'music':
                response = request('POST', prepared['url'], key, body, timeout=REQUEST_TIMEOUT)
                job_id = response.get('task_id') if isinstance(response, dict) else None
                music_urls(job_id)
                state['provider_job_id'], state['status'] = job_id, 'submitted'
                base.save_state(state_path, state)
                _retrieve_music(key, state, output_path, timeout, index)
            else:
                data, media = request('POST', prepared['url'], key, body, timeout=min(timeout, state['timeouts']['request_seconds']), raw=True)
                data = validate_result(service, operation, prepared['body'], data, media)
                state['artifacts'] = [base.save_artifact(output_path, data, prepared['url'])]
                state['status'] = 'completed'
    except (Blocked, WalletApproval, RequestError, JobError, PollTimeout) + _LOCAL_ERRORS as error:
        if state is None:
            return _failure(service, operation, error)
        _record_error(state, error)
    return _finish(state_path, state)


def resume(service, operation, state_path, output_path, timeout, index=None):
    state = None
    try:
        if service != 'music' or operation != 'generate':
            raise Blocked('Only music jobs have a supported resume endpoint')
        _validator()
        check_paths(None, state_path, output_path, 'resume')
        if not base.finite(timeout) or timeout <= 0:
            raise Blocked('Positive finite timeout required')
        loaded = _validator().strict_json(Path(state_path).read_bytes())
        if (not isinstance(loaded, dict) or loaded.get('service') != service or loaded.get('operation') != operation
                or loaded.get('status') not in {'submitted', 'timed_out', 'poll_error', 'completed'}
                or not isinstance(loaded.get('artifacts'), list) or not isinstance(loaded.get('errors'), list)):
            raise Blocked('State is not resumable; reconcile without resubmitting')
        music_urls(loaded.get('provider_job_id'))
        variable, keys = resolve_keys(service)
        key = resume_key(loaded, variable, keys)
        state = loaded
        _retrieve_music(key, state, output_path, timeout, 0 if index is None else index)
    except (Blocked, RequestError, JobError, PollTimeout) + _LOCAL_ERRORS as error:
        if state is None:
            return _failure(service, operation, error)
        _record_error(state, error)
    return _finish(state_path, state)


def execute(args):
    check_paths(args.payload, args.state, args.output, args.command)
    if args.command == 'resume':
        return resume(args.service, args.operation, args.state, args.output, args.timeout, args.index)
    payload = load_payload(args.payload, args.operation)
    if args.command == 'check':
        return check(args.service, args.operation, payload, args.state, args.wallet_limit)
    return run(args.service, args.operation, payload, args.state, args.output, args.wallet_limit, args.timeout, args.index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'run', 'resume'))
    parser.add_argument('operation')
    parser.add_argument('--service', choices=sorted(OPERATIONS), default=SERVICE)
    parser.add_argument('--payload', help='Private JSON request file; omit for books list or music resume')
    parser.add_argument('--state', default='job-state.json', help='Private durable state outside the checkout')
    parser.add_argument('--output', default='result.bin', help='New artifact path outside the checkout')
    parser.add_argument('--wallet-limit', type=float, help='Roubles explicitly approved by the user')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='Deadline in seconds')
    parser.add_argument('--index', type=int, help='Music track index, 0 or 1')
    args = parser.parse_args()
    if args.operation not in OPERATIONS[args.service]:
        parser.error('Operation is not supported by the selected service')
    if args.wallet_limit is not None and (not math.isfinite(args.wallet_limit) or args.wallet_limit <= 0):
        parser.error('--wallet-limit must be finite and positive')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    if args.index is not None and args.index not in (0, 1):
        parser.error('--index must be 0 or 1')
    if args.command == 'resume' and args.service != 'music':
        parser.error('resume applies only to music')
    try:
        result = execute(args)
    except (Blocked, WalletApproval, RequestError, JobError, PollTimeout) + _LOCAL_ERRORS as error:
        result = _failure(args.service, args.operation, error)
    result = {**state_template('standalone', args.service), **result}
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in {'completed', 'ready'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
