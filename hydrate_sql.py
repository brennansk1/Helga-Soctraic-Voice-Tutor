import os, sys, time, logging
ROOT = '/Users/brennankelley/Desktop/helga-live'
sys.path.insert(0, ROOT)
os.environ.setdefault('DATA_ROOT', ROOT + '/data')
os.environ.setdefault('LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
os.environ.setdefault('OLLAMA_URL', 'http://localhost:11434')
os.environ.setdefault('OLLAMA_MODEL', 'nail-35b-a3b-ctx')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from services.common.storage import StorageManager
from services.core.course_builder import ContentHydrator, SyllabusAuditor

UID = 'course_7c8e2bce'
storage = StorageManager(os.environ['DATA_ROOT'])
def say(m): print(f'  [status] {m}', flush=True)

t0 = time.time()
aud = SyllabusAuditor(status_callback=say, storage=storage)
try:
    aud.audit(UID, target_depth=3)
finally:
    aud.close()
print(f'  audit done in {time.time()-t0:.0f}s', flush=True)

h = ContentHydrator(status_callback=say, course_depth=3, storage=storage, mastery=4)
try:
    h.hydrate(UID)
finally:
    h.close()

c = storage.courses.get_course(UID)
print(f'\nHYDRATED in {time.time()-t0:.0f}s')
print(f'status : {c.get("status")}')
import glob
print('md files:', len(glob.glob(f'{ROOT}/data/courses/{UID}/content/*.md')))
