"""全局配置：全部从环境变量读取，变量名严格对齐 contract/API-CONTRACT.md 第四部分。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """所有字段都有默认值，保证不配 .env 也能起来（本地开发用）。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 数据库 ---
    MYSQL_HOST: str = "mysql"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "energy_tds"
    MYSQL_USER: str = "energy"
    MYSQL_PASSWORD: str = "energy123"

    # --- Redis ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # --- 后端自身 ---
    BACKEND_PORT: int = 8000
    JWT_SECRET: str = "energy-tds-demo-secret-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_SECONDS: int = 28800  # 8 小时，契约 1.3
    # 托管私钥的 SM4 加密密钥种子。与 JWT_SECRET 分开，
    # 因为 JWT_SECRET 每次安装会重新随机生成，而托管私钥一旦加密就必须能解回来。
    KEY_CUSTODY_SECRET: str = "energy-tds-key-custody-2026"

    # --- 算法服务（乙提供，甲调用）---
    ALGO_SERVICE_URL: str = "http://algo-service:8100"
    ALGO_TIMEOUT: float = 15.0

    # --- 运行期开关 ---
    DEBUG: bool = False
    # 启动时等待 MySQL 就绪的最长秒数
    DB_WAIT_TIMEOUT: int = 120

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            f"?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
