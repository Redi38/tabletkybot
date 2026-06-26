import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    # Telegram
    bot_token: str
    # Webhook
    webhook_host: str
    webhook_path: str = "/webhook"
    webhook_secret: str = "change_me"
    webhook_port: int = 8443
    webhook_cert: str = "webhook.pem"
    webhook_key: str = "webhook.key"
    # NVIDIA API
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.1-70b-instruct"
    nvidia_vision_model: str = "meta/llama-3.2-11b-vision-instruct"
    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    ollama_vision_model: str = "llama3.2-vision:latest"
    # PostgreSQL
    postgres_user: str = "botuser"
    postgres_password: str = "botpassword"
    postgres_db: str = "medbot"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_host}{self.webhook_path}"


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN")
    nvidia_api_key = os.getenv("NVIDIA_API_KEY")
    webhook_host = os.getenv("WEBHOOK_HOST")

    if not bot_token:
        raise ValueError("BOT_TOKEN не знайдено у змінних середовища")
    if not nvidia_api_key:
        raise ValueError("NVIDIA_API_KEY не знайдено у змінних середовища")
    if not webhook_host:
        raise ValueError("WEBHOOK_HOST не знайдено у змінних середовища")

    return Config(
        bot_token=bot_token,
        webhook_host=webhook_host,
        webhook_path=os.getenv("WEBHOOK_PATH", "/webhook"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "change_me"),
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8443")),
        webhook_cert=os.getenv("WEBHOOK_CERT", "webhook.pem"),
        webhook_key=os.getenv("WEBHOOK_KEY", "webhook.key"),
        nvidia_api_key=nvidia_api_key,
        nvidia_base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        nvidia_model=os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
        nvidia_vision_model=os.getenv("NVIDIA_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct"),
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3"),
        ollama_vision_model=os.getenv("OLLAMA_VISION_MODEL", "llama3.2-vision:latest"),
        postgres_user=os.getenv("POSTGRES_USER", "botuser"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "botpassword"),
        postgres_db=os.getenv("POSTGRES_DB", "medbot"),
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        redis_password=os.getenv("REDIS_PASSWORD"),
    )
