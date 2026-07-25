from config import Config


def _fake_config(nvidia_api_key="test-key"):
    return Config(
        bot_token="t",
        webhook_host="https://example.com",
        nvidia_api_key=nvidia_api_key,
        nvidia_base_url="https://nim.example.com/v1",
        nvidia_model="test-model",
        ollama_url="http://localhost:11434",
        ollama_model="llama3",
    )
