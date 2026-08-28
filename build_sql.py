import os, sys, time, logging
ROOT = '/Users/brennankelley/Desktop/helga-live'
sys.path.insert(0, ROOT)
os.environ.setdefault('DATA_ROOT', ROOT + '/data')
os.environ.setdefault('LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
os.environ.setdefault('OLLAMA_URL', 'http://localhost:11434')
os.environ.setdefault('OLLAMA_MODEL', 'nail-35b-a3b-ctx')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from services.common.storage import StorageManager
from services.core.course_builder import SkeletonBuilder

def say(msg):
    print(f'  [status] {msg}', flush=True)

storage = StorageManager(os.environ['DATA_ROOT'])
b = SkeletonBuilder(storage=storage, status_callback=say,
                    scope=3, mastery=4, starting_from=2,
                    teaching_style='direct, practical, industry-facing')

topic = ('Advanced SQL for analytics engineering: CTEs, window functions with '
         'explicit frames, recursive queries, set operations, execution plans '
         'and query optimisation')

t0 = time.time()
uid = b.build(topic)
print(f'\nRESULT uid={uid} in {time.time()-t0:.0f}s', flush=True)
if uid:
    c = storage.courses.get_course(uid)
    print(f'title  : {c.get("title")}')
    print(f'status : {c.get("status")}')
    print(f'domain : {c.get("teaching_domain")}')
    print(f'kinds  : {c.get("concept_kinds")}')
    mods = c.get('modules') or []
    print(f'modules: {len(mods)}')
    for m in mods:
        print(f'  - {m.get("title")}')
