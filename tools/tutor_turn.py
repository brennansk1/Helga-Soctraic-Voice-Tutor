"""Produce ONE real tutor turn for a concept, against the live model.

Usage: python3 tools/tutor_turn.py "<concept title>" ["<learner says>"]

This is the only check that answers "can it teach this?". Everything else —
status flags, kind tallies, test suites — verifies the machinery around the
turn. Structurally-clean-but-hollow is this project's signature failure, so the
turn itself gets read.
"""
import os, sys, json, logging
ROOT = '/Users/brennankelley/Desktop/helga-live'
sys.path.insert(0, ROOT)
os.environ.setdefault('DATA_ROOT', ROOT + '/data')
os.environ.setdefault('LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
os.environ.setdefault('OLLAMA_URL', 'http://localhost:11434')
os.environ.setdefault('OLLAMA_MODEL', 'nail-35b-a3b-ctx')
logging.basicConfig(level=logging.WARNING)

from services.common.storage import StorageManager
from services.common.prompts import get_typed_socratic_prompt
from services.common import llm_utils
from services.domains import registry

UID = os.environ.get('COURSE_UID', 'course_7c8e2bce')
want = (sys.argv[1] if len(sys.argv) > 1 else '').lower()
learner = sys.argv[2] if len(sys.argv) > 2 else "I'm not sure, can you explain?"

storage = StorageManager(os.environ['DATA_ROOT'])
course = storage.courses.get_course(UID)
allc = [c for m in course.get('modules', []) for u in m.get('units', [])
        for l in u.get('lessons', []) for c in l.get('concepts', [])]

target = next((c for c in allc if want in (c.get('title') or '').lower()), None)
if not target:
    print('no such concept. available:')
    for c in allc[:40]:
        print('  -', c.get('title'), '->', c.get('concept_kind'))
    sys.exit(1)

kind = target.get('concept_kind')
content = storage.courses.get_concept_content(UID, target['uid']) or ''
print(f'CONCEPT : {target.get("title")}')
print(f'KIND    : {kind}')
print(f'CONTENT : {len(content)} chars')
if not content.strip():
    print('\n!! no content — this concept is a dead end for the learner')
    sys.exit(2)

# THE KIND MUST BE A (domain, kind) TUPLE, exactly as the FSM builds it
# (`kind_arg = (domain, kind) if kind else None`, fsm_logic.py:1858).
# A BARE STRING silently yields no guidance at all: prompts.py sets
# `dk, kind = None, None` for anything that is not a 2-tuple, so `for_domain`
# gets None and `prompt_line` is never called. Passing "SYNTAX" here produced a
# turn with no kind guidance and looked exactly like a broken domain layer.
domain = registry.domain_of(course) or 'computer_science'
kind_arg = (domain, kind) if kind else None
print(f'DOMAIN  : {domain}')

messages = get_typed_socratic_prompt(
    'probe', content[:4000], [(learner, None)], concept_kind=kind_arg)

# The FSM flattens the messages array into a system prompt plus one user
# message before dispatching (fsm_logic._call_llm), so the harness does the
# same rather than inventing a second call shape.
sys_parts, user_parts = [], []
for m in messages:
    role, text = m.get('role'), (m.get('content') or '')
    (sys_parts if role == 'system' else user_parts).append(text)

# Probe the LINE the prompt actually embeds, not the long-form guidance.
_ext = registry.for_domain(domain)
_line = ''
try:
    _line = (_ext.prompt_line(kind) or '') if (_ext and kind) else ''
except Exception:
    pass
_sys = '\n'.join(sys_parts)
print(f'KIND GUIDANCE IN PROMPT: {bool(_line) and _line[:50] in _sys}')
print(f'\nLEARNER : {learner}')
print('-' * 68)
out = llm_utils.llm_generate(
    prompt='\n\n'.join(user_parts).strip() or 'Continue the conversation.',
    sys_prompt='\n\n'.join(sys_parts).strip(),
    max_tokens=400)
print((out or '(no response)').strip())
print('-' * 68)
