"""仅供本地浏览器回归的无外部调用后端；生产启动不加载本文件。"""
import os
import sys
import time
import json

sys.path.insert(0, os.environ['WQ_PROJECT_ROOT'])
from backend.main import app
from backend.routers import agent


def frame(event, value):
    return f'event: {event}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n'


def fake_agent(session, user_text, auto=False):
    session.messages.append({'role': 'user', 'content': user_text})
    yield frame('tool', {'name': 'get_data_summary', 'args': []})
    time.sleep(0.15)
    yield frame('tool_done', {'name': 'get_data_summary', 'ok': True})
    yield frame('delta', {'text': '这是第一段流式内容。'})
    time.sleep(1.0)
    yield frame('delta', {'text': '这是第二段完整结论。'})
    session.messages.append({'role': 'assistant', 'content': '这是第一段流式内容。这是第二段完整结论。'})
    yield frame('done', {'turns': 1})


agent.run_agent = fake_agent
