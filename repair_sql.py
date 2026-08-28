"""Re-hydrate only the concepts that failed, so the course can EARN "ready".

`partial` renders as a disabled card (courses.js:167), so one failure in 108
makes the course unopenable. The gate is right — "ready" is a promise that a
learner can open any concept and find something real — so this repairs the
failures rather than loosening it.
"""
import os, sys, glob, time, logging
ROOT = '/Users/brennankelley/Desktop/helga-live'
sys.path.insert(0, ROOT)
os.environ.setdefault('DATA_ROOT', ROOT + '/data')
os.environ.setdefault('LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
os.environ.setdefault('OLLAMA_URL', 'http://localhost:11434')
os.environ.setdefault('OLLAMA_MODEL', 'nail-35b-a3b-ctx')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from services.common.storage import StorageManager
from services.core.course_builder import ContentHydrator

UID = 'course_7c8e2bce'
MARK = '[Hydration failed]'
storage = StorageManager(os.environ['DATA_ROOT'])

def failed_concepts():
    bad = []
    for f in glob.glob(f'{ROOT}/data/courses/{UID}/content/*.md'):
        try:
            if MARK in open(f, encoding='utf-8').read():
                bad.append(os.path.basename(f)[:-3])
        except Exception:
            pass
    return bad

course = storage.courses.get_course(UID)
titles = {c['uid']: c.get('title')
          for m in course.get('modules', []) for u in m.get('units', [])
          for l in u.get('lessons', []) for c in l.get('concepts', [])}

for attempt in range(1, 4):
    bad = failed_concepts()
    print(f'\n=== attempt {attempt}: {len(bad)} failed concepts ===', flush=True)
    for u in bad:
        print('   -', titles.get(u, u), flush=True)
    if not bad:
        break
    h = ContentHydrator(course_depth=3, storage=storage, mastery=4,
                        status_callback=lambda m: print(f'  [status] {m}', flush=True))
    try:
        # Delete the failed markdown so the hydrator regenerates it rather than
        # treating the stub as already-present content.
        for u in bad:
            p = f'{ROOT}/data/courses/{UID}/content/{u}.md'
            if os.path.exists(p):
                os.unlink(p)
        h.hydrate(UID)
    finally:
        h.close()

bad = failed_concepts()
course = storage.courses.get_course(UID)
print(f'\nremaining failures: {len(bad)}')
if not bad and course.get('status') != 'ready':
    course['status'] = 'ready'
    storage.courses.update_course(UID, course)
    print('status -> ready (every concept has real content)')
print('final status:', storage.courses.get_course(UID).get('status'))
