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
    webhook_drop_pending_updates: bool = True
    # NVIDIA API
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.1-70b-instruct"
    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
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
    # Backup (Oracle Object Storage, S3-compatible)
    backup_s3_bucket: str | None = None
    backup_s3_endpoint_url: str | None = None
    backup_s3_access_key: str | None = None
    backup_s3_secret_key: str | None = None
    backup_s3_region: str = "eu-frankfurt-1"
    backup_retention_days: int = 14
    # Voice
    nvidia_riva_function_id: str = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
    # Feedback
    admin_chat_id: int | None = None
    # Admin panel auth
    admin_panel_username: str = "admin"
    admin_panel_password_hash: str = ""
    admin_panel_session_secret: str = "change_me"
    # Internal admin<->bot sync endpoint auth
    sync_secret: str = ""

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
        raise ValueError("BOT_TOKEN not found in environment variables")
    if not nvidia_api_key:
        raise ValueError("NVIDIA_API_KEY not found in environment variables")
    if not webhook_host:
        raise ValueError("WEBHOOK_HOST not found in environment variables")

    admin_chat_id_raw = os.getenv("ADMIN_CHAT_ID")

    admin_panel_password_hash = os.getenv("ADMIN_PANEL_PASSWORD_HASH", "")
    admin_panel_session_secret = os.getenv("ADMIN_PANEL_SESSION_SECRET", "change_me")
    if admin_panel_password_hash and admin_panel_session_secret == "change_me":
        raise ValueError(
            "ADMIN_PANEL_SESSION_SECRET must be changed to your own random value "
            '(e.g.: python -c "import secrets; print(secrets.token_hex(32))")'
        )

    sync_secret = os.getenv("SYNC_SECRET", "")

    webhook_secret = os.getenv("WEBHOOK_SECRET", "change_me")
    if webhook_secret == "change_me":
        raise ValueError(
            "WEBHOOK_SECRET must be changed to your own random value "
            '(e.g.: python -c "import secrets; print(secrets.token_hex(32))"). '
            "It is compared against Telegram's X-Telegram-Bot-Api-Secret-Token header on every "
            "incoming webhook request, so a default/guessable value defeats that check."
        )

    return Config(
        bot_token=bot_token,
        webhook_host=webhook_host,
        webhook_path=os.getenv("WEBHOOK_PATH", "/webhook"),
        webhook_secret=webhook_secret,
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8443")),
        webhook_cert=os.getenv("WEBHOOK_CERT", "webhook.pem"),
        webhook_key=os.getenv("WEBHOOK_KEY", "webhook.key"),
        webhook_drop_pending_updates=os.getenv("WEBHOOK_DROP_PENDING_UPDATES", "true").strip().lower()
        in ("1", "true", "yes", "on"),
        nvidia_api_key=nvidia_api_key,
        nvidia_base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        nvidia_model=os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3"),
        postgres_user=os.getenv("POSTGRES_USER", "botuser"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "botpassword"),
        postgres_db=os.getenv("POSTGRES_DB", "medbot"),
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        redis_password=os.getenv("REDIS_PASSWORD"),
        backup_s3_bucket=os.getenv("BACKUP_S3_BUCKET"),
        backup_s3_endpoint_url=os.getenv("BACKUP_S3_ENDPOINT_URL"),
        backup_s3_access_key=os.getenv("BACKUP_S3_ACCESS_KEY"),
        backup_s3_secret_key=os.getenv("BACKUP_S3_SECRET_KEY"),
        backup_s3_region=os.getenv("BACKUP_S3_REGION", "eu-frankfurt-1"),
        backup_retention_days=int(os.getenv("BACKUP_RETENTION_DAYS", "14")),
        nvidia_riva_function_id=os.getenv("NVIDIA_RIVA_FUNCTION_ID", "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"),
        admin_chat_id=int(admin_chat_id_raw) if admin_chat_id_raw else None,
        admin_panel_username=os.getenv("ADMIN_PANEL_USERNAME", "admin"),
        admin_panel_password_hash=admin_panel_password_hash,
        admin_panel_session_secret=admin_panel_session_secret,
        sync_secret=sync_secret,
    )
