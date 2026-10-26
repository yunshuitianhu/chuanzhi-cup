"""Verify the actual SDK wire body, without making an external model request."""
import json
import unittest
from unittest.mock import patch
import httpx
from openai import OpenAI
from backend.agent import loop
from backend.session import Session

class DeepSeekWireContract(unittest.TestCase):
    def test_agent_uses_current_model_with_explicit_non_thinking_tool_protocol(self):
        requests=[]
        def respond(request):
            body=json.loads(request.content)
            requests.append(body)
            self.assertEqual(body['model'],'deepseek-flash')
            self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertIn('tools',body)
            chunk={'id':'test-local','object':'chat.completion.chunk','created':1,'model':'deepseek-flash',
                'choices':[{'index':0,'delta':{'content':'这是SDK协议检查，不是水质结论。'},'finish_reason':'stop'}]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                text='data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n')
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client=OpenAI(api_key='local-dummy-test',base_url='https://api.deepseek.com',http_client=http)
            with patch.object(loop,'OpenAI',return_value=client),patch.object(loop,'resolve_api_key',return_value='local-dummy-test'):
                frames=list(loop.run_agent(Session(id='wire-test'),'解释数据适用范围'))
        self.assertEqual(len(requests),1)
        self.assertTrue(any('succeeded' in frame for frame in frames))
