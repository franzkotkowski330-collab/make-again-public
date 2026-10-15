"""语音网关冒烟测试:默认强制 mock(不耗真实 key)。

要打真实接口:去掉 mock_speech=True 即可(需 .env 配好 MAAS_API_KEY)。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from config import Settings
from gateway.speech import SpeechClient

c = SpeechClient(settings=Settings(mock_speech=True))
print("mock 模式:", c.mock)
print("TTS:", c.tts("刚吹过晚风的巷口飘着糖炒栗子的香。"))
print("ASR:", c.transcribe("https://cos.example.com/xxx.mp4"))
