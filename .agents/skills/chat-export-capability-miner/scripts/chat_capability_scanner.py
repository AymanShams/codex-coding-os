from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


REPO_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
MCP_RE = re.compile(r"\b[A-Za-z0-9_.@/-]{2,80}(?:\s+|-|_)?MCP(?:\s+server)?\b", re.I)
SKILL_SIGNAL_RE = re.compile(
    r"\b(skill|SKILL\.md|agent skill|convert(?:ed)? to (?:a )?skill|install(?:ed)? skill|plugin|MCP|mcp server|tool|app|repo|repository|github)\b",
    re.I,
)
DECISION_RE = re.compile(
    r"\b(install|installed|useful|worth|candidate|backlog|pilot|project-local|reference only|skip|avoid|overlap|duplicate|better than|reusable|mine|convert)\b",
    re.I,
)

STOP_DOMAINS = {
    "chat.openai.com",
    "chatgpt.com",
    "openai.com",
    "help.openai.com",
    "google.com",
    "docs.google.com",
    "drive.google.com",
    "youtube.com",
    "youtu.be",
}


def rel(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def context(text: str, start: int, end: int, width: int = 220) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def score_context(ctx: str) -> int:
    score = 0
    if DECISION_RE.search(ctx):
        score += 4
    if re.search(r"\b(MCP|plugin|skill|tool|repo|github|install|pilot)\b", ctx, re.I):
        score += 2
    if re.search(r"\b(do not|don't|avoid|skip|not useful|overlap|duplicate)\b", ctx, re.I):
        score += 1
    return score


def domain(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].lower()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roots", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalogue", required=True)
    args = parser.parse_args()

    roots = [Path(p) for p in args.roots]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    catalogue_text = Path(args.catalogue).read_text(encoding="utf-8", errors="ignore").lower()

    md_files: list[Path] = []
    for root in roots:
        md_files.extend(root.rglob("*.md"))

    repo_hits: dict[str, dict] = {}
    url_hits: dict[str, dict] = {}
    mcp_hits: dict[str, dict] = {}
    skill_signal_files: dict[str, dict] = {}
    tool_name_counter: Counter[str] = Counter()

    tool_like = re.compile(
        r"\b(?:use|install|try|pilot|review|add|consider|compare|benchmark|build with|built with)\s+([A-Z][A-Za-z0-9_. -]{2,60}|[@a-z0-9_.-]+/[a-z0-9_.-]+)",
        re.I,
    )

    for i, file_path in enumerate(md_files, 1):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            continue
        rel_path = rel(file_path, roots)

        for match in REPO_RE.finditer(text):
            repo = match.group(1).rstrip(".")
            ctx = context(text, match.start(), match.end())
            hit = repo_hits.setdefault(
                repo.lower(),
                {"name": repo, "count": 0, "files": Counter(), "examples": [], "score": 0, "in_catalogue": repo.lower() in catalogue_text},
            )
            hit["count"] += 1
            hit["files"][rel_path] += 1
            hit["score"] += score_context(ctx)
            if len(hit["examples"]) < 4:
                hit["examples"].append({"file": rel_path, "context": ctx})

        for match in URL_RE.finditer(text):
            url = match.group(0).rstrip(".,")
            d = domain(url)
            if d in STOP_DOMAINS or d.endswith(".openai.com") or "github.com" in d:
                continue
            ctx = context(text, match.start(), match.end())
            key = d
            hit = url_hits.setdefault(
                key,
                {"domain": d, "count": 0, "files": Counter(), "examples": [], "score": 0, "in_catalogue": d in catalogue_text},
            )
            hit["count"] += 1
            hit["files"][rel_path] += 1
            hit["score"] += score_context(ctx)
            if len(hit["examples"]) < 3:
                hit["examples"].append({"file": rel_path, "url": url, "context": ctx})

        for match in MCP_RE.finditer(text):
            raw = re.sub(r"\s+", " ", match.group(0)).strip("`*.,:; ")
            ctx = context(text, match.start(), match.end())
            key = raw.lower()
            hit = mcp_hits.setdefault(
                key,
                {"name": raw, "count": 0, "files": Counter(), "examples": [], "score": 0, "in_catalogue": raw.lower() in catalogue_text},
            )
            hit["count"] += 1
            hit["files"][rel_path] += 1
            hit["score"] += score_context(ctx)
            if len(hit["examples"]) < 3:
                hit["examples"].append({"file": rel_path, "context": ctx})

        if SKILL_SIGNAL_RE.search(text) and DECISION_RE.search(text):
            title = ""
            for line in text.splitlines()[:20]:
                clean = line.strip("# ").strip()
                if clean:
                    title = clean[:140]
                    break
            key = rel_path
            excerpt_match = DECISION_RE.search(text)
            excerpt = context(text, excerpt_match.start(), excerpt_match.end()) if excerpt_match else ""
            skill_signal_files[key] = {
                "file": rel_path,
                "title": title,
                "size": len(text),
                "excerpt": excerpt,
            }

        for match in tool_like.finditer(text):
            name = match.group(1).strip("`* .,:;")
            if 2 < len(name) < 80:
                tool_name_counter[name] += 1

    def finish_hits(items: dict[str, dict]) -> list[dict]:
        results = []
        for item in items.values():
            files = item.pop("files")
            item["file_count"] = len(files)
            item["top_files"] = [f"{name} ({count})" for name, count in files.most_common(5)]
            results.append(item)
        return sorted(results, key=lambda x: (not x["in_catalogue"], x["score"], x["count"], x["file_count"]), reverse=True)

    data = {
        "roots": [str(r) for r in roots],
        "markdown_file_count": len(md_files),
        "repos": finish_hits(repo_hits),
        "domains": finish_hits(url_hits),
        "mcps": finish_hits(mcp_hits),
        "skill_signal_files": sorted(skill_signal_files.values(), key=lambda x: x["size"], reverse=True),
        "tool_like_names": tool_name_counter.most_common(200),
    }

    (out_dir / "capability_scan.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Chat Export Capability Scan",
        "",
        f"Markdown files scanned: {len(md_files)}",
        "",
        "## Top GitHub Repos Not Already In Catalogue",
        "",
    ]
    for hit in [h for h in data["repos"] if not h["in_catalogue"]][:80]:
        lines.append(f"- `{hit['name']}` count={hit['count']} files={hit['file_count']} score={hit['score']}")
        for ex in hit["examples"][:1]:
            lines.append(f"  - {ex['file']}: {ex['context'][:450]}")
    lines.extend(["", "## Top External Domains Not Already In Catalogue", ""])
    for hit in [h for h in data["domains"] if not h["in_catalogue"]][:80]:
        lines.append(f"- `{hit['domain']}` count={hit['count']} files={hit['file_count']} score={hit['score']}")
        for ex in hit["examples"][:1]:
            lines.append(f"  - {ex['file']}: {ex['context'][:450]}")
    lines.extend(["", "## Top MCP Mentions Not Already In Catalogue", ""])
    for hit in [h for h in data["mcps"] if not h["in_catalogue"]][:80]:
        lines.append(f"- `{hit['name']}` count={hit['count']} files={hit['file_count']} score={hit['score']}")
        for ex in hit["examples"][:1]:
            lines.append(f"  - {ex['file']}: {ex['context'][:450]}")
    lines.extend(["", "## Skill-Signal Chat Files", ""])
    for hit in data["skill_signal_files"][:120]:
        lines.append(f"- `{hit['file']}` size={hit['size']} title={hit['title']}")
        lines.append(f"  - {hit['excerpt'][:450]}")
    (out_dir / "capability_scan_summary.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
