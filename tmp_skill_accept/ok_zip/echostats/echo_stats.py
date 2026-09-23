"""echostats 工具脚本：统计目标文本文件的行数与字符数（--path 入参）。"""
import argparse


def main() -> None:
    """解析 --path 并输出 lines/chars 统计。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    args = ap.parse_args()
    try:
        with open(args.path, encoding="utf-8", errors="replace") as f:
            t = f.read()
        print(f"lines={t.count(chr(10)) + 1} chars={len(t)}")
    except OSError as e:
        print(f"error: {e}")


if __name__ == "__main__":
    main()
