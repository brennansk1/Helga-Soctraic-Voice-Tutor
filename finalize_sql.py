"""Post-hydration: drop the scaffolding unit, re-type every concept, report.

Must run AFTER hydration. The hydrator holds the course dict in memory and
writes it back when it finishes, so anything written to structure.json while it
runs is silently overwritten.
"""
import os, sys, json, logging
ROOT = '/Users/brennankelley/Desktop/helga-live'
sys.path.insert(0, ROOT)
os.environ.setdefault('DATA_ROOT', ROOT + '/data')
os.environ.setdefault('LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
os.environ.setdefault('OLLAMA_URL', 'http://localhost:11434')
os.environ.setdefault('OLLAMA_MODEL', 'nail-35b-a3b-ctx')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from collections import Counter
from services.common.storage import StorageManager
from services.core.course_builder import SkeletonBuilder

UID = 'course_7c8e2bce'
storage = StorageManager(os.environ['DATA_ROOT'])
course = storage.courses.get_course(UID)

def concepts(c):
    return [x for m in c.get('modules', []) for u in m.get('units', [])
            for l in u.get('lessons', []) for x in l.get('concepts', [])]

before = len(concepts(course))
print(f'before: {before} concepts')
print('kinds :', dict(Counter(c.get("concept_kind") for c in concepts(course))))

# 1. Drop the scaffolding.
tally = SkeletonBuilder.prune_placeholder_scaffolding(SkeletonBuilder, course)
print(f'\npruned: {tally}')

# 2. Re-type everything with the corrected rules. Clearing the old kinds first
#    is the point of the exercise — the stored values were produced by the
#    bare-"vs" pattern and by a prompt that never defined TOOL_BOUNDARY.
for c in concepts(course):
    c.pop('concept_kind', None)

b = SkeletonBuilder(storage=storage, status_callback=lambda m: None,
                    scope=3, mastery=4, starting_from=2)
b._classify_concepts_by_domain(course, course.get('title', ''))

after = concepts(course)
print(f'\nafter : {len(after)} concepts')
print('kinds :', dict(Counter(c.get("concept_kind") for c in after)))
print('\nTOOL_BOUNDARY (tutor will refuse to answer — should be few and genuine):')
for c in after:
    if c.get('concept_kind') == 'TOOL_BOUNDARY':
        print('  -', c.get('title'))

storage.courses.update_course(UID, course)
print('\nsaved. status:', storage.courses.get_course(UID).get('status'))
