import os
import sys
import tempfile

# backend/ 를 import 경로에 넣어 `services.xxx`로 불러올 수 있게 한다
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 테스트 중 로그 파일은 임시 폴더에 쓰고, 실제 Supabase로는 절대 보내지 않는다
os.environ["LOG_FILE_DIR"] = tempfile.mkdtemp(prefix="cine-test-logs-")
for name in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.pop(name, None)
