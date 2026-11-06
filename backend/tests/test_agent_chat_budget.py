"""Regression: a compatible provider can keep calling tools when tools are omitted."""
from contextlib import nullcontext
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec('chromadb'), 'requires full backend chat dependencies')
class ChatBudgetTests(unittest.TestCase):
    def setUp(self):
        from app.services import chat_service
        self.service = chat_service
        self.tool_call = SimpleNamespace(id='call', function=SimpleNamespace(name='get_note_content', arguments='{}'))

    def response(self, content=None, tools=None):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tools))])

    def invoke(self, responses):
        create = Mock(side_effect=responses)
        gpt = SimpleNamespace(model='test-model', client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        with patch.object(self.service, 'VectorStoreManager') as store, \
             patch.object(self.service.ProviderService, 'get_provider_by_id', return_value={
                 'api_key': 'test', 'base_url': 'https://example.invalid', 'type': 'custom', 'name': 'test'}), \
             patch.object(self.service.GPTFactory, 'from_config', return_value=gpt), \
             patch.object(self.service, 'resources', return_value=nullcontext()), \
             patch.object(self.service, 'execute_tool', return_value='{"markdown":"未提供日活数据"}') as execute:
            store.return_value.query.return_value = []
            result = self.service.chat('task', '日活是多少？', [], 'provider', 'test-model')
        return result, create, execute

    def test_final_call_explicitly_disables_tools_and_preserves_three_round_budget(self):
        result, create, execute = self.invoke([
            self.response(tools=[self.tool_call]), self.response(tools=[self.tool_call]),
            self.response(tools=[self.tool_call]), self.response('视频未提供日活数据。')])
        self.assertEqual(result['answer'], '视频未提供日活数据。')
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(create.call_count, 4)
        final = create.call_args.kwargs
        self.assertEqual(final['tool_choice'], 'none')
        self.assertTrue(final['tools'])
        self.assertIn('禁止继续调用工具', final['messages'][-1]['content'])

    def test_provider_that_ignores_tool_choice_does_not_complete_with_empty_answer(self):
        with self.assertRaisesRegex(ValueError, '工具调用结束'):
            self.invoke([self.response(tools=[self.tool_call])] * 4)

    def test_empty_or_blank_direct_answer_is_a_failure(self):
        for content in [None, '', '   ']:
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, '有效文本'):
                self.invoke([self.response(content)])

    def test_normal_direct_answer_still_returns_without_tools(self):
        result, create, execute = self.invoke([self.response('视频未提供。')])
        self.assertEqual(result['answer'], '视频未提供。')
        self.assertEqual(create.call_count, 1)
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
