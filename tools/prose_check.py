#!/usr/bin/env python3
"""
Prose checker for Chinese technical documentation.

Every rule below comes from a published source, not from taste:

  zh-style-guide (yikeke/zh-style-guide)
    文档结构样式/句子   -- avoid one-comma-to-the-end run-on sentences
    语言风格/客观礼貌   -- avoid personification, rhetorical questions, exclamations
    语言风格/对话式     -- "conversational" does not mean spoken language; colloquial
                           Chinese is verbose and loose, avoid it in technical docs

  humanizer-zh (op7418/Humanizer-zh, from blader/humanizer)
    §2  single-sentence closers and dramatic fragments
    §3  aphorism and pseudo-depth (figurative language standing in for the fact)
    §4  runway openers ("here is what you need to know")
    §24 first sentence restating the heading

Usage:
    python tools/prose_check.py README.md docs/*.md
    python tools/prose_check.py --stat README.md docs/*.md

Exit code is 1 when anything is reported, so it can gate a commit.
"""

import argparse
import re
import sys

# ---------------------------------------------------------------- vocabulary

# 语言风格/客观礼貌: do not attribute human traits or actions to objects.
PERSONIFICATION = {
    "吃掉": "空闲区被消耗应写「被占用」",
    "咬": "拟人",
    "啃": "拟人",
    "塞不进去": "拟人化的「塞」；写「无法插入」",
    "塞下": "拟人化的「塞」；写「容纳」",
    "塞进": "拟人化的「塞」；写「插入」",
    "活着": "拟人",
    "跑起来": "写「运行」",
    "跑得慢": "写「耗时较长」",
    "长得像": "拟人",
    "吐": "拟人",
}

# 语言风格/对话式: colloquial Chinese is verbose and loose.
COLLOQUIAL = {
    "搞定": "口语",
    "说白了": "口语",
    "其实": "口语，多为可删的填充词",
    "基本上": "口语，改为具体范围",
    "差不多": "口语，改为具体数值",
    "一下子": "口语",
    "玩意儿": "口语",
    "弄": "口语，改为具体动作",
    "干嘛": "口语",
    "行不行": "口语",
    "好不好": "口语",
    "回头": "口语",
}

# Patterns that a plain word list cannot express.
REGEX_RULES = [
    (r"(?<!空)洞", "比喻", "「洞」是口语比喻，写「空闲区」"),
    (r"不动", "口语", "写「不变」"),
    (r"碰不到|碰不着", "口语", "写「不涉及」"),
    (r"挑[几选]", "口语", "写「选取」"),
    (r"打出来|打印出来", "口语", "写「输出」"),
    (r"跑一遍", "口语", "写「执行一次」"),
    (r"装下", "口语", "写「容纳」"),
    (r"来测", "口语", "写「用于测试」"),
    (r"的时候", "口语", "写「时」"),
    (r"插不进去|塞不进去|放不进去", "口语", "写「无法插入」"),
    (r"越挑", "口语", "写「对…要求越高」"),
    (r"删掉一条", "口语", "写「删除后」"),
    (r"(?<!需)要跨", "口语", "写「需要跨越」"),
    (r"统一成", "口语", "写「统一为」"),
    (r"表达不出来", "口语", "写「无法表达」"),
    (r"看哪个|看哪边", "口语", "写「取决于」"),
]

# humanizer-zh §4 / §24: openers that announce instead of stating.
TEMPLATE_OPENERS = [
    (r"这个仓库是", "§24 自我描述式开头"),
    (r"本仓库是", "§24 自我描述式开头"),
    (r"本文将", "§4 起跑式铺垫"),
    (r"下面(我们)?(来)?(介绍|看看|说明)", "§4 起跑式铺垫"),
    (r"接下来(我们)?(来)?(看|介绍|说明)", "§4 起跑式铺垫"),
    (r"值得注意的是", "§4 空转过渡"),
    (r"总而言之|综上所述", "§31 套话收尾"),
    (r"不是[^，。]{1,12}而是", "§1 假对比"),
]

# ---------------------------------------------------------------- helpers

CODE_FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`]*`")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean(line):
    """Strip markup that is not prose."""
    line = INLINE_CODE.sub("CODE", line)
    line = IMAGE.sub("", line)
    line = LINK.sub(r"\1", line)
    return line


def sentences(line):
    """Split on full stops, keep the terminator for length accounting."""
    out = []
    buf = ""
    for ch in line:
        buf += ch
        if ch in "。！？":
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return out


def fragments(sentence):
    body = sentence.rstrip("。！？")
    return [p for p in re.split(r"[，；：]", body) if p.strip()]


# ---------------------------------------------------------------- checks

def check_line(path, lineno, raw):
    hits = []
    if not raw.strip() or raw.lstrip().startswith(("#", "|", ">", "!", "- ", "* ", "```")):
        return hits

    text = clean(raw)

    for word, why in PERSONIFICATION.items():
        if word in text:
            hits.append((path, lineno, "拟人/比喻", word, why))

    for word, why in COLLOQUIAL.items():
        if word in text:
            hits.append((path, lineno, "口语", word, why))

    for pattern, rule, why in REGEX_RULES:
        m = re.search(pattern, text)
        if m:
            hits.append((path, lineno, rule, m.group(0), why))

    for pattern, why in TEMPLATE_OPENERS:
        m = re.search(pattern, text)
        if m:
            hits.append((path, lineno, "模板句", m.group(0), why))

    if "！" in text:
        hits.append((path, lineno, "感叹句", "！", "客观礼貌：非警示场景不用感叹号"))
    if re.search(r"[^`]？", text) and not raw.strip().startswith("#"):
        hits.append((path, lineno, "反问句", "？", "客观礼貌：不用反问句"))

    for s in sentences(text):
        parts = fragments(s)
        if len(parts) >= 4:
            hits.append((path, lineno, "一逗到底", s.strip()[:40],
                         "句子：单句逗号过多，应断句"))
        if len(parts) >= 3:
            avg = sum(len(p) for p in parts) / float(len(parts))
            if avg < 10:
                hits.append((path, lineno, "碎句堆砌", s.strip()[:40],
                             "§2 短分句堆叠，缺连接"))
        if len(s.strip()) > 100:
            hits.append((path, lineno, "超长句", "%d 字" % len(s.strip()),
                         "句子：建议不超过 100 字"))
    return hits


def check_file(path):
    hits = []
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    body = CODE_FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), body)
    for i, line in enumerate(body.splitlines(), 1):
        hits.extend(check_line(path, i, line))
    return hits


def main():
    ap = argparse.ArgumentParser(description="Chinese technical prose checker")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--stat", action="store_true", help="summary only")
    args = ap.parse_args()

    all_hits = []
    for f in args.files:
        all_hits.extend(check_file(f))

    if not args.stat:
        for path, lineno, rule, token, why in all_hits:
            print("%s:%d  [%s] %s  -- %s" % (path, lineno, rule, token, why))

    print("\n%d 处" % len(all_hits))
    by_rule = {}
    for _, _, rule, _, _ in all_hits:
        by_rule[rule] = by_rule.get(rule, 0) + 1
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print("  %-10s %d" % (rule, n))
    return 1 if all_hits else 0


if __name__ == "__main__":
    sys.exit(main())
