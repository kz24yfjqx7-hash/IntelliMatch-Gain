"""把 01_schema.sql 和 02_seed.sql 导进 MySQL。

不用 mysql 命令行客户端——宿主机上不一定装了它（比如 MySQL 跑在容器里的时候）。
直接用 pymysql，反正后端本来就依赖它。
"""
import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import settings  # noqa: E402


def 拆语句(sql: str):
    """按分号拆，但要认得引号里的分号——种子数据里全是 JSON，里面分号很多。"""
    语句, 缓冲, 引号, 转义 = [], [], None, False
    for ch in sql:
        if 转义:
            缓冲.append(ch)
            转义 = False
            continue
        if ch == "\\" and 引号:
            缓冲.append(ch)
            转义 = True
            continue
        if 引号:
            缓冲.append(ch)
            if ch == 引号:
                引号 = None
            continue
        if ch in "'\"`":
            引号 = ch
            缓冲.append(ch)
            continue
        if ch == ";":
            s = "".join(缓冲).strip()
            if s:
                语句.append(s)
            缓冲 = []
            continue
        缓冲.append(ch)
    尾 = "".join(缓冲).strip()
    if 尾:
        语句.append(尾)
    # 去掉纯注释行组成的空语句
    return [s for s in 语句 if any(
        l.strip() and not l.strip().startswith("--") for l in s.splitlines())]


def 导入(路径: Path, conn) -> int:
    语句 = 拆语句(路径.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        for i, s in enumerate(语句, 1):
            try:
                cur.execute(s)
            except Exception as exc:  # noqa: BLE001
                raise SystemExit(f"\n{路径.name} 第 {i} 条语句失败：\n{s[:300]}\n\n{exc}")
    conn.commit()
    return len(语句)


def main() -> None:
    conn = pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        charset="utf8mb4", autocommit=False,
    )
    这里 = Path(__file__).parent
    for 文件 in ("01_schema.sql", "02_seed.sql"):
        n = 导入(这里 / 文件, conn)
        print(f"  ✓ {文件}  {n} 条语句")
    conn.close()


if __name__ == "__main__":
    main()
