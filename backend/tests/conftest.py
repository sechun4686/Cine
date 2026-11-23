import os
import sys
import tempfile

# backend/ 를 import 경로에 넣어 `services.xxx`로 불러올 수 있게 한다
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 테스트 중 로그 파일은 임시 폴더에 쓰고, 실제 Supabase로는 절대 보내지 않는다
os.environ["LOG_FILE_DIR"] = tempfile.mkdtemp(prefix="cine-test-logs-")
for name in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.pop(name, None)


import pytest  # noqa: E402

_SUPABASE_VARS = ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")


@pytest.fixture(autouse=True)
def _no_real_supabase(monkeypatch):
    """
    테스트마다 Supabase 설정을 지운다.
    main.py가 import될 때 load_dotenv(override=True)로 backend/.env를 다시 읽기 때문에,
    위의 os.environ.pop만으로는 로컬 .env의 실제 Supabase 키가 되살아나
    테스트 로그가 실제 DB에 저장될 수 있다. Supabase 동작을 확인하는 테스트는 이 다음에 setenv로 가짜 값을 넣는다.
    """
    for name in _SUPABASE_VARS:
        monkeypatch.delenv(name, raising=False)
