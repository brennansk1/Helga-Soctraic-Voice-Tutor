import sys, os
sys.path.insert(0, '/tmp/helga-main')
sys.path.insert(0, '/tmp/helga-main/services/web-ui')
os.environ.setdefault('DATA_ROOT', '/tmp/task0/audit')
os.environ.setdefault('FLASK_SECRET_KEY', 'audit-only')
import app as webui
webui.app.run(host='127.0.0.1', port=5098, debug=False, use_reloader=False, threaded=True)
