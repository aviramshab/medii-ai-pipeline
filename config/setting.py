import os
from pydantic_settings import BaseSettings
from urllib.parse import quote_plus
from typing import ClassVar


class Settings(BaseSettings):

    # --- ANTHROPIC CONFIGURATION ---
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    max_model_tokens: int = 200000
    max_output_tokens: int = 4096
    max_chars_per_call: int = 8000

    max_segment_tokens: int | None = None

    # This will store the loaded prompt file
    system_prompt: str = ""

    # Namespace constants
    W_NS: ClassVar[str] = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    NSMAP: ClassVar[dict] = {"w": W_NS}

    # --- DATABASE CONFIGURATION ---
    mysql_host: str = "13.49.227.187"
    mysql_port: int = 3306
    mysql_database: str = "medii_api_db"
    mysql_user: str = "root"
    mysql_password: str = ""

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = True

    base_path: str = ""

    @property
    def base_dir(self) -> str:
        return self.base_path or os.getcwd()

    @property
    def data_directory(self) -> str:
        return os.path.join(self.base_dir, "uploads")

    @property
    def upload_directory(self) -> str:
        return self.data_directory

    @property
    def output_directory(self) -> str:
        return self.data_directory

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prompt",
            "template_3.txt" 
        )

        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt = f.read()
            print(f" Loaded system prompt from: {prompt_path}")
        except Exception as e:
            print(" Could not load system prompt file:", e)

    @property
    def database_url(self) -> str:
        encoded_user = quote_plus(self.mysql_user)
        encoded_password = quote_plus(self.mysql_password)

        return (
            f"mysql+pymysql://{encoded_user}:{encoded_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Create global settings instance
settings = Settings()

print("BASE_DIR:", settings.base_dir)
print("DATA_DIR:", settings.data_directory)
print(f"[Config] MySQL: {settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}")