import unittest
from unittest.mock import Mock, patch

from trendradar.ai.client import AIClient


class AIClientTests(unittest.TestCase):
    @patch("trendradar.ai.client._get_shared_llm_callable")
    def test_chat_uses_shared_llm_client(self, mock_get_shared_llm_callable):
        llm_call = Mock(return_value="LLM OK")
        mock_get_shared_llm_callable.return_value = llm_call

        client = AIClient(
            {
                "MODEL": "deepseek/deepseek-chat",
                "API_KEY": "",
                "API_BASE": "https://example.com/v1",
                "TEMPERATURE": 0.4,
                "TIMEOUT": 30,
            }
        )

        response = client.chat(
            [
                {"role": "system", "content": "你是财经助手"},
                {"role": "user", "content": "请总结今天市场风险"},
            ],
            temperature=0.1,
            timeout=60,
        )

        self.assertEqual(response, "LLM OK")
        llm_call.assert_called_once_with(
            "请总结今天市场风险",
            system_prompt="你是财经助手",
            model="deepseek/deepseek-chat",
            temperature=0.1,
            base_url="https://example.com/v1",
            api_key="",
            timeout=60,
        )

    @patch("trendradar.ai.client._get_shared_llm_callable")
    def test_validate_config_does_not_require_explicit_ai_api_key(self, mock_get_shared_llm_callable):
        mock_get_shared_llm_callable.return_value = Mock(return_value="ok")

        client = AIClient({"MODEL": "deepseek/deepseek-chat", "API_KEY": ""})
        valid, error = client.validate_config()

        self.assertTrue(valid)
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
