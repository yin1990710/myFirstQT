#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean_file.py
功能：删除 myFirstQT 文件夹下所有文件夹名称后缀（末尾8位数字日期 YYYYMMDD）
      在 3 日（自然日）之前的子文件夹（含恰好 3 日前当天）。
      - 只处理当前脚本所在目录下的直接子目录（不递归向下）。
      - 文件夹名称末尾没有合法 YYYYMMDD 8 位数字日期后缀的，一律保留不动。
      - 仅删除"目录"，带日期后缀的普通文件（如 大盘趋势报告YYYYMMDD.png）不处理。
"""

import os
import re
import shutil
from datetime import datetime, timedelta


# 文件夹末尾必须是 YYYYMMDD 8 位数字
DATE_SUFFIX_RE = re.compile(r'(\d{8})$')


def get_target_dir():
    """清理目标目录：clean_file.py 所在目录（即 myFirstQT）"""
    return os.path.dirname(os.path.abspath(__file__))


def extract_folder_date(folder_name):
    """从文件夹名末尾提取 8 位日期；若不存在或非法则返回 None"""
    m = DATE_SUFFIX_RE.search(folder_name)
    if not m:
        return None
    date_str = m.group(1)
    try:
        return datetime.strptime(date_str, '%Y%m%d').date()
    except ValueError:
        # 形如 00000000 或 99991299 之类非法日历
        return None


def clean_old_folders(days_ago=3, dry_run=False):
    """
    删除目标目录下，末尾日期后缀 <= (今日 - days_ago) 的文件夹。
    :param days_ago: 几日阈值（含当天），默认 3 即 3 日前
    :param dry_run: True 则只打印将删除的清单，不真删
    :return: (deleted_list, kept_list, skipped_list)
    """
    target_dir = get_target_dir()
    today = datetime.now().date()
    cutoff = today - timedelta(days=days_ago)

    print("=" * 70)
    print(f"🗑️  清理 {target_dir} 下 文件夹名称后缀 <= {cutoff.strftime('%Y%m%d')} "
          f"(今日 {today.strftime('%Y%m%d')} 的 {days_ago} 日前) 的子目录")
    print("=" * 70)

    deleted = []
    kept = []
    skipped = []   # 非目录 / 无合法日期后缀

    try:
        entries = sorted(os.listdir(target_dir))
    except FileNotFoundError:
        print(f"❌ 目标目录不存在: {target_dir}")
        return deleted, kept, skipped

    for name in entries:
        path = os.path.join(target_dir, name)
        # 只处理目录（非文件、非符号链接）
        if not os.path.isdir(path):
            skipped.append((name, "非目录"))
            continue

        folder_date = extract_folder_date(name)
        if folder_date is None:
            skipped.append((name, "无合法日期后缀"))
            continue

        if folder_date <= cutoff:
            # 命中清理条件
            action = "[预删]" if dry_run else "✅ 删除"
            print(f"{action}: {name}  (日期 {folder_date.strftime('%Y%m%d')} <= 阈值 {cutoff.strftime('%Y%m%d')})")
            if not dry_run:
                try:
                    shutil.rmtree(path)
                    deleted.append((name, folder_date))
                except Exception as e:
                    print(f"    ❗ 删除失败 {name}: {e}")
                    skipped.append((name, f"删除失败: {e}"))
            else:
                deleted.append((name, folder_date))
        else:
            kept.append((name, folder_date))

    print("-" * 70)
    print(f"📊 汇总：命中清理 {len(deleted)} 个 | 保留 {len(kept)} 个 | 跳过 {len(skipped)} 个")
    if kept:
        preview = "、".join(f"{n}({d.strftime('%Y%m%d')})" for n, d in kept[:6])
        print(f"  保留前6例：{preview}{'…' if len(kept) > 6 else ''}")
    if skipped:
        preview_skip = "、".join(f"{n}[{r}]" for n, r in skipped[:6])
        print(f"  跳过前6例：{preview_skip}{'…' if len(skipped) > 6 else ''}")
    print("=" * 70)
    return deleted, kept, skipped


def main():
    # 默认实跑。若加参数 --dry-run 则只预览不删除
    import sys
    dry_run = "--dry-run" in sys.argv
    clean_old_folders(days_ago=3, dry_run=dry_run)


if __name__ == "__main__":
    main()
