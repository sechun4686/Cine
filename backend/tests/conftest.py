import os
import sys

# backend/ 를 import 경로에 넣어 `services.xxx`로 불러올 수 있게 한다
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
