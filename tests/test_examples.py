"""Parse examples and equivalent native rules without performing API calls."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT=Path(__file__).parents[1]


def split(text):
    if not text.startswith('---\n'): return '',text.strip()
    end=text.index('\n---\n',4)
    return text[4:end],text[end+5:].strip()


def scopes(head,key):
    lines=head.splitlines()
    try: start=lines.index(key+':')+1
    except ValueError: return []
    result=[]
    for line in lines[start:]:
        if not line.startswith('  - '): break
        result.append(json.loads(line[4:]))
    return result


class ExampleTests(unittest.TestCase):
    def test_manifest_json_is_a_valid_request(self):
        spec=importlib.util.spec_from_file_location('example_api',ROOT/'scripts/service_api.py')
        api=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        source=(ROOT/'SKILL.md').read_text(encoding='utf-8')
        examples=re.findall(r'```json\n(.*?)\n```',source,re.S)
        self.assertEqual(len(examples),1)
        service=ROOT.name.removeprefix('neuraldeep-')
        operation={'chat':'complete','embeddings':'embed','rerank':'rerank','tts':'speak','music':'generate',
                   'pii':'anonymize','decisions':'decide','books':'search','drift':'chat'}[service]
        for example in examples: api.plan(service,operation,json.loads(example))

    def test_shell_examples_parse_without_execution(self):
        count=0
        for path in ROOT.glob('*.md'):
            if path.is_symlink(): continue
            for block in re.findall(r'```bash\n(.*?)\n```',path.read_text(encoding='utf-8'),re.S):
                result=subprocess.run(['bash','-n'],input=block,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(result.returncode,0,result.stderr)
                count+=1
        self.assertGreater(count,1)

    def test_native_rule_bodies_and_scopes_are_equivalent(self):
        cursor={p.stem:p for p in (ROOT/'.cursor/rules').glob('*.mdc')}
        self.assertEqual(set(cursor),{'workflow','client','documentation'})
        for host in ('coddy','claude'):
            native={p.stem:p for p in (ROOT/f'.{host}/rules').glob('*.md')}
            self.assertEqual(set(cursor),set(native))
            for name,path in cursor.items():
                with self.subTest(host=host,rule=name):
                    head,body=split(path.read_text(encoding='utf-8'))
                    other_head,other_body=split(native[name].read_text(encoding='utf-8'))
                    self.assertEqual(body,other_body)
                    self.assertEqual(scopes(head,'globs'),scopes(other_head,'paths'))
                    self.assertEqual('alwaysApply: true' in head,not bool(scopes(other_head,'paths')))

    def test_codex_hook_is_wired_and_attaches_always_on_rules(self):
        config=json.loads((ROOT/'.codex/hooks.json').read_text(encoding='utf-8'))
        self.assertIn('SessionStart',config['hooks'])
        self.assertIn('PreToolUse',config['hooks'])
        event={'hook_event_name':'SessionStart','session_id':'offline-skill-tests','source':'startup'}
        env={name:os.environ[name] for name in ('PATH','TMPDIR') if name in os.environ}
        result=subprocess.run(['python3','-B',str(ROOT/'.codex/hooks/attach_rules.py')],input=json.dumps(event),
                              env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        context=json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('.cursor/rules/workflow.mdc',context)
        self.assertNotIn('.cursor/rules/client.mdc',context)
        self.assertFalse((ROOT/'.codex/rules.md').exists())

    def test_source_ci_supports_oldest_and_latest_python(self):
        workflow=ROOT/'.github/workflows/tests.yml'
        if not workflow.exists():
            self.skipTest('The distribution intentionally excludes source CI')
        source=workflow.read_text(encoding='utf-8')
        self.assertIn('3.10',source)
        self.assertIn('3.x',source)
        self.assertIn('unittest discover',source)


if __name__=='__main__': unittest.main()
