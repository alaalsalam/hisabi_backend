import unittest
from unittest.mock import patch

from hisabi_backend.api.v1 import ai_advisor


class TestOpenAITransport(unittest.TestCase):
    @patch("requests.post")
    def test_sol_uses_reasoning_parameters_for_text_and_receipts(self, post):
        post.return_value.json.return_value = {"choices": [{"message": {"content": "result"}}]}
        settings = {"openai_api_key": "fixture", "openai_text_model": "gpt-6.2-sol"}

        self.assertEqual(
            ai_advisor._call_openai([{"role": "system", "content": "instructions"}], settings, 900),
            "result",
        )
        self.assertEqual(
            ai_advisor._vision_openai(b"fixture", "image/png", "instructions", settings), "result"
        )

        text_call, vision_call = post.call_args_list
        for call in (text_call, vision_call):
            self.assertTrue(call.args[0].endswith("/v1/chat/completions"))
            payload = call.kwargs["json"]
            self.assertEqual(payload["model"], "gpt-6.2-sol")
            self.assertEqual(payload["reasoning_effort"], "low")
            self.assertEqual(payload["max_completion_tokens"], 5000)
            self.assertNotIn("max_tokens", payload)
            self.assertNotIn("temperature", payload)
        self.assertEqual(text_call.kwargs["json"]["messages"][0]["role"], "developer")
        image = vision_call.kwargs["json"]["messages"][0]["content"][1]
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))

    @patch("requests.post")
    def test_classic_models_keep_their_existing_parameters(self, post):
        post.return_value.json.return_value = {"choices": [{"message": {"content": "result"}}]}
        settings = {"openai_api_key": "fixture", "openai_text_model": "gpt-4o-mini"}
        ai_advisor._call_openai([{"role": "system", "content": "instructions"}], settings, 900)
        text_payload = post.call_args.kwargs["json"]
        self.assertEqual(text_payload["temperature"], 0.3)
        self.assertEqual(text_payload["max_tokens"], 900)
        self.assertEqual(text_payload["messages"][0]["role"], "system")
        ai_advisor._vision_openai(b"fixture", "image/png", "instructions", settings)
        vision_payload = post.call_args.kwargs["json"]
        self.assertEqual(vision_payload["temperature"], 0.2)
        self.assertEqual(vision_payload["max_tokens"], 1500)
