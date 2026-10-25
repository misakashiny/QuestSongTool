"""从 cost-meter 账本里算出 9/25 当天（尤其 20:00~22:00）的花费构成。

账本只按天聚合，所以小时维度要用「会话的 at 时间戳 + 会话累计」来近似：
每个会话的 at 是它最后一次更新的时刻，据此把当天会话分组到小时。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

LEDGER = Path.home() / ".dsh" / "storages" / "cost-meter" / "ledger.json"
TZ = timezone(timedelta(hours=8))  # 本地时区（中国）
DAY = "2026-09-25"


def load() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def main() -> int:
    ledger = load()
    day = ledger["days"][DAY]
    print(f"=== {DAY} 全天 ===")
    print(f"  调用次数 {day['calls']}  输入 {day['input']:,}  "
          f"输出 {day['output']:,}  缓存读 {day['cacheRead']:,}  推理 {day.get('reasoning', 0):,}")
    print(f"  成本 ${day['cost']:.4f}  ≈ ¥{day['cost'] * 7.2:.2f}")
    print("  分模型:")
    for key, value in day["byProviderModel"].items():
        print(f"    {key}: {value['calls']} 次调用, ${value['cost']:.4f}, "
              f"输入 {value['input']:,}, 输出 {value['output']:,}, 缓存读 {value['cacheRead']:,}")
    print(f"  会话数 {len(day['sessions'])}")

    print("\n=== 按会话结束时刻排序（本地时间）===")
    sessions = sorted(day["sessions"], key=lambda s: s.get("at", 0))
    for session in sessions:
        at = session.get("at", 0) / 1000
        when = datetime.fromtimestamp(at, TZ).strftime("%H:%M:%S")
        print(f"  {when}  {session['id'][:44]:<46} "
              f"调用 {session['calls']:>4}  ${session['cost']:.4f}  ¥{session['cost'] * 7.2:>5.2f}  "
              f"输入 {session['input']:>9,}  输出 {session['output']:>7,}  缓存读 {session['cacheRead']:>11,}")

    print("\n=== 按小时汇总（按会话结束时刻归属）===")
    buckets: dict[int, dict[str, float]] = {}
    for session in sessions:
        hour = datetime.fromtimestamp(session.get("at", 0) / 1000, TZ).hour
        bucket = buckets.setdefault(hour, {"cost": 0.0, "calls": 0, "sessions": 0})
        bucket["cost"] += session["cost"]
        bucket["calls"] += session["calls"]
        bucket["sessions"] += 1
    for hour in sorted(buckets):
        bucket = buckets[hour]
        bar = "█" * max(1, int(bucket["cost"] / 0.02))
        print(f"  {hour:02d}:00  ${bucket['cost']:.4f}  ¥{bucket['cost'] * 7.2:>5.2f}  "
              f"{bucket['sessions']:>2} 会话 {bucket['calls']:>4} 调用  {bar}")

    print("\n=== 图片附件（每个都会进入上下文并被反复重发）===")
    root = Path.home() / ".dsh" / "attachments" / "v1" / "request-images"
    images = []
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                images.append(path)
    total_bytes = sum(p.stat().st_size for p in images)
    print(f"  数量 {len(images)}，合计 {total_bytes / 1024 / 1024:.2f} MB")
    per_day: dict[str, int] = {}
    for path in images:
        key = datetime.fromtimestamp(path.stat().st_mtime, TZ).strftime("%m-%d")
        per_day[key] = per_day.get(key, 0) + 1
    for key in sorted(per_day, reverse=True)[:6]:
        print(f"    {key}: {per_day[key]} 张")

    print("\n=== 结论参考：单张图的单价（按 deepseek-v4-flash 缓存未命中 $0.15/M）===")
    for size_kb in (43, 92, 100):
        tokens = size_kb * 1024 / 4  # 粗估：1 token ≈ 4 字节
        print(f"  {size_kb} KB 图 ≈ {int(tokens):,} tokens ≈ ${tokens / 1_000_000 * 0.15:.4f}/次（未命中缓存）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
