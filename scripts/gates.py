#!/usr/bin/env python3
"""Rules no compiler or test enforces, checked by scanning the tree.

    gates.py [--root DIR] [--only NAME] [--list]

Each gate prints every violation as path:line. A gate that scanned no files fails,
because a gate looking at nothing would pass forever. scripts/test_gates.py and the
gates area of scripts/fault_matrix.py show each one failing on a planted violation.

Adding a gate: write a function taking (root, files) and returning violations, and
add it to GATES with the paths it applies to.

Exit codes: 0 every gate passed, 1 at least one failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# data/ holds the days and docs/local/ the demo's built engine and extract: both gitignored.
SKIPPED_DIRS = {".git", ".venv", ".claude", "__pycache__", "data", "local"}
CXX_SUFFIXES = {".hpp", ".cpp", ".h", ".cc"}
WIRE_CODE = ("include", "src", "tools")
PRODUCTION_CODE = ("include", "src")
# Fed untrusted reference files, so they are held to the parser's rule on aborting.
REFERENCE_PARSERS = ("tests/trades/reference_text.hpp", "tests/trades/cross_stats.hpp",
                     "tests/trades/midas.hpp")
ALL_CODE = ("include", "src", "tools", "tests", "fuzz", "bench")
# Prose may discuss what the project holds back and cite where it came from. Code,
# scripts, configuration and results may not touch it.
PROSE = {".md"}

BYTE_TYPES = {"char", "unsigned char", "signed char", "std::byte", "byte", "std::uint8_t",
              "uint8_t", "std::int8_t", "int8_t", "void"}

COMMENT_OR_LITERAL = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL)
NAMED_POINTER_CAST = re.compile(
    r"\b(reinterpret_cast|static_cast|const_cast)\s*<\s*((?:const\s+|volatile\s+)*)"
    r"([\w:]+(?:\s+[\w:]+)*)\s*(?:const\s*)?[*&]")
# A C-style cast to a pointer to any type, followed by what it casts: `(const Header*)src`.
# A declaration's unnamed parameter, `f(Header*) const`, is not followed by an operand.
C_STYLE_POINTER_CAST = re.compile(
    r"\(\s*((?:[A-Za-z_][\w:]*(?:\s*<[^<>()]*>)?\s+)*[A-Za-z_][\w:]*(?:\s*<[^<>()]*>)?)"
    r"\s*(?:const\s*)?\*+\s*(?:const\s*)?\)"
    r"(?=\s*[\w(&*])(?!\s*(?:const|volatile|override|final|noexcept|throw)\b)")
# A reinterpret_cast whose target names no * or &: a pointer through an alias, `WordPtr`,
# unless it is the integer a pointer is cast to.
ALIAS_CAST = re.compile(r"\breinterpret_cast\s*<\s*((?:const\s+|volatile\s+)*[\w:]+"
                        r"(?:\s*<[^<>]*>)?)\s*>")
POINTER_INTEGERS = {"std::uintptr_t", "uintptr_t", "std::intptr_t", "intptr_t"}
LIFETIME_START = re.compile(r"\bstart_lifetime_as(?:_array)?\b")
BRANCH_HINT = re.compile(
    r"\[\[\s*(?:likely|unlikely)\s*\]\]|__builtin_expect\w*|__builtin_prefetch|_mm_prefetch"
    r"|\b(?:LIKELY|UNLIKELY)\s*\(|\bprefetch[rw]?\s*\(")
MEASURED = re.compile(r"//.*\bmeasured:\s*\S")
WORKFLOW_USES = re.compile(r"""^[\s-]*["']?uses["']?\s*:\s*(.*)$""")
PINNED_ACTION = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*\S+")
HOME_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+")
PRIVATE_ADDRESS = re.compile(
    r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
    r"|\b192\.168\.\d{1,3}\.\d{1,3}\b|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
HELD_VENDOR = re.compile(r"c[b]oe", re.IGNORECASE)
PREPROCESSOR_CONDITION = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif|elifdef|elifndef)\b")
ABORTING_CALL = re.compile(
    r"\bassert\s*\(|\bthrow\b|\babort\s*\(|\b__builtin_trap\b|\bstd::terminate\b"
    r"|\bstd::get\s*<|\.value\s*\(\s*\)|\bstd::unreachable\b")

# Names that may appear in results: the host labels, and CI's login. Anything else that names
# this machine or its user is private, and so is every name in ITCH_PRIVATE_NAMES.
PUBLIC_NAMES = {"debian", "runner", "localhost", "root", "user"}
PRIVATE_NAMES_ENV = "ITCH_PRIVATE_NAMES"

# T-MM-1: the attribution is computed from fills and marks alone. Neither it nor any header
# it includes may include the ledger or name it in code.
LEDGER_FREE = ("include/itch/mm/attribution.hpp",)
QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)
LEDGER_NAME = re.compile(r"\bLedger\b|\bledger\w*")

# The demo page's own files, held to the styling rules the brief sets for it.
DEMO_PAGE = "docs"
DEMO_SUFFIXES = {".html", ".css", ".js", ".mjs"}
CSS_DECLARATION = re.compile(r"([\w-]+)\s*:\s*([^;{}\n]+)")
CSS_VARIABLE = re.compile(r"(--[\w-]+)\s*:\s*([^;{}\n]+)")
JS_COLOUR_SETTING = re.compile(
    r"\b(?:background(?:Color)?|fillStyle)\s*[:=]\s*[\"'`]([^\"'`]+)[\"'`]")
HTML_BGCOLOR = re.compile(r"\bbgcolor\s*=\s*[\"']?([^\"'\s>]+)", re.IGNORECASE)
HEX_COLOUR = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
RGB_COLOUR = re.compile(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)")
VAR_USE = re.compile(r"var\(\s*(--[\w-]+)")
# Named colours that are cream or off-white.
CREAM_NAMES = {"ivory", "beige", "linen", "oldlace", "floralwhite", "cornsilk", "seashell",
               "antiquewhite", "snow", "whitesmoke", "ghostwhite", "honeydew", "mintcream",
               "azure", "aliceblue", "lavenderblush", "papayawhip", "blanchedalmond", "bisque",
               "lemonchiffon", "lightyellow", "wheat", "navajowhite", "moccasin", "gainsboro"}
ITALIC = re.compile(r"font-style\s*:\s*(?:italic|oblique)|\bfontStyle\s*=\s*[\"'`](?:italic|oblique)"
                    r"|\bfont\s*[:=]\s*[\"'`]?[^;\"'`\n]*\b(?:italic|oblique)\b", re.IGNORECASE)
HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
ITALIC_TAG = re.compile(r"<(em|i|cite|var|dfn)\b", re.IGNORECASE)
NUMBERED_LABEL = re.compile(r"(?<![\w.,:#-])0[1-9](?![\w.,:%-])")
LEADING_ZERO_COUNTER = re.compile(r"decimal-leading-zero", re.IGNORECASE)
BORDER_RADIUS = re.compile(r"(?:border(?:-[a-z]+)*-radius|borderRadius)\s*[:=]\s*[\"'`]?([^;\"'`}\n]+)",
                           re.IGNORECASE)
RADIUS_VALUE = re.compile(r"(\d*\.?\d+)\s*(px|rem|em|%|vh|vw)?")
MOST_RADIUS_PX = 6.0
GRADIENT = re.compile(r"(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(|"
                      r"create(?:Linear|Radial|Conic)Gradient\s*\(", re.IGNORECASE)
SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
CODE_BLOCK = re.compile(r"<(pre|code)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)
STYLE_ATTRIBUTE = re.compile(r"\bstyle\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
TAG = re.compile(r"<[^>]*>")
JS_STRING = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`", re.DOTALL)
JS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

# scripts/test_fetch_data.py asserts that the data scripts do not name the held vendor,
# so it has to name it.
HELD_VENDOR_EXEMPT = {"scripts/test_fetch_data.py"}


def code_only(text):
    """Blanks comments and literals, keeping every newline so that line numbers hold."""
    return COMMENT_OR_LITERAL.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def read_text(path):
    """The text of a file, or None for a binary one."""
    data = path.read_bytes()
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def walk(root, top=None):
    """Every file under root, skipping build output and everything else not in the repo."""
    base = root / top if top else root
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        return
    for path in sorted(base.iterdir()):
        # By name, file or not: in a git worktree .git is a file naming the main repository.
        if path.name in SKIPPED_DIRS or (path.parent == root and path.name.startswith("build")):
            continue
        if path.is_dir():
            yield from walk(root, path.relative_to(root))
        else:
            yield path


def cxx_files(root, tops):
    return [path for top in tops for path in walk(root, top) if path.suffix in CXX_SUFFIXES]


def relative(root, path):
    return path.relative_to(root).as_posix()


def line_of(text, position):
    return text.count("\n", 0, position) + 1


def wire_pointer_cast(root, files):
    violations = []
    for path in files:
        code = code_only(read_text(path) or "")
        for match in NAMED_POINTER_CAST.finditer(code):
            pointee = " ".join(match[3].split())
            if pointee not in BYTE_TYPES:
                violations.append((path, line_of(code, match.start()),
                                   f"{match[1]} to {pointee}"))
        for match in C_STYLE_POINTER_CAST.finditer(code):
            pointee = " ".join(word for word in match[1].split()
                               if word not in ("const", "volatile"))
            if pointee not in BYTE_TYPES:
                violations.append((path, line_of(code, match.start()),
                                   f"C-style cast to a pointer to {pointee}"))
        for match in ALIAS_CAST.finditer(code):
            target = " ".join(word for word in match[1].split()
                              if word not in ("const", "volatile"))
            if target not in POINTER_INTEGERS:
                violations.append((path, line_of(code, match.start()),
                                   f"reinterpret_cast to {target}, a pointer through an alias"))
        for match in LIFETIME_START.finditer(code):
            violations.append((path, line_of(code, match.start()), "start_lifetime_as"))
    return violations


def bare_branch_hint(root, files):
    violations = []
    for path in files:
        raw = (read_text(path) or "").splitlines()
        code = code_only("\n".join(raw)).splitlines()
        for number, line in enumerate(code, start=1):
            match = BRANCH_HINT.search(line)
            if match is None:
                continue
            context = raw[number - 1] + "\n" + (raw[number - 2] if number > 1 else "")
            if not MEASURED.search(context):
                violations.append((path, number, f"{match[0]} with no // measured: reason"))
    return violations


def scan_lines(pattern, what):
    def scan(root, files):
        violations = []
        for path in files:
            for number, line in enumerate((read_text(path) or "").splitlines(), start=1):
                match = pattern.search(line)
                if match is not None:
                    violations.append((path, number, what.format(match[0])))
        return violations
    return scan


def held_vendor(root, files):
    kept = [path for path in files
            if path.suffix not in PROSE and relative(root, path) not in HELD_VENDOR_EXEMPT]
    return scan_lines(HELD_VENDOR, "held vendor named: {}")(root, kept)


def private_names():
    """The login and host names of the machine running the gates, and any listed in the
    environment, each at least four characters and not a public label."""
    names = {getpass.getuser()}
    host = socket.gethostname()
    names |= {host, host.split(".")[0]}
    names |= {name.strip() for name in os.environ.get(PRIVATE_NAMES_ENV, "").split(",")}
    return sorted(name for name in names
                  if len(name) >= 4 and name.lower() not in PUBLIC_NAMES)


def private_name(root, files):
    """A results file, flamegraphs included, that names this machine or its user."""
    names = private_names()
    if not names:
        return []
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(name) for name in names)
                         + r")(?![\w-])", re.IGNORECASE)
    return scan_lines(pattern, "private name: {}")(root, files)


def results_files(root):
    """Every results file, and every file that is not prose elsewhere: a name in code or a
    script would reach a result. Prose outside results/ may name the project's public
    GitHub account, which shares the login name."""
    kept = set(walk(root, "results"))
    kept |= {path for path in walk(root) if path.suffix not in PROSE}
    return sorted(kept)


def preprocessor_condition(root, files):
    return scan_lines(PREPROCESSOR_CONDITION, "conditional compilation: {}")(root, files)


def aborting_call(root, files):
    violations = []
    for path in files:
        code = code_only(read_text(path) or "")
        for number, line in enumerate(code.splitlines(), start=1):
            match = ABORTING_CALL.search(line)
            if match is not None:
                violations.append((path, number, f"can abort on wire content: {match[0]}"))
    return violations


def toolchain_path(root, files):
    build_files = [path for path in files if path.suffix in {".cmake", ".sh", ".py", ".yml", ".yaml"}
                   or path.name in {"CMakeLists.txt", "CMakePresets.json"}]
    return scan_lines(re.compile(r"\.tool" r"chain/"), "hardcoded toolchain path: {}")(
        root, build_files)


def ledger_free_files(root):
    """The attribution header and every repository header it includes, transitively."""
    found = []
    pending = [root / name for name in LEDGER_FREE]
    while pending:
        path = pending.pop()
        if path in found or not path.is_file():
            continue
        found.append(path)
        for name in QUOTED_INCLUDE.findall(read_text(path) or ""):
            pending.append(root / "include" / name)
    return found


def reads_ledger(root, files):
    violations = []
    for path in files:
        text = read_text(path) or ""
        for match in QUOTED_INCLUDE.finditer(text):
            if "ledger" in match[1].lower():
                violations.append((path, line_of(text, match.start()),
                                   f"includes {match[1]}"))
        code = code_only(text)
        for match in LEDGER_NAME.finditer(code):
            violations.append((path, line_of(code, match.start()), f"names {match[0]}"))
    return violations


def demo_files(root):
    return [path for path in walk(root, DEMO_PAGE) if path.suffix in DEMO_SUFFIXES]


def blank(match):
    """The match with everything but its newlines blanked, so that line numbers hold."""
    return re.sub(r"[^\n]", " ", match.group(0))


def keep_only(text, spans):
    """The text with everything outside the spans blanked, newlines kept."""
    kept = list(re.sub(r"[^\n]", " ", text))
    for start, end in spans:
        kept[start:end] = text[start:end]
    return "".join(kept)


def is_script(path):
    return path.suffix in (".js", ".mjs")


def css_of(path, text):
    """The CSS a file carries, at its own positions: a stylesheet whole, less its comments; a
    page's style blocks and style attributes; nothing of a script."""
    if path.suffix == ".css":
        return re.sub(r"/\*.*?\*/", blank, text, flags=re.DOTALL)
    if path.suffix == ".html":
        spans = [match.span(1) for pattern in (STYLE_BLOCK, STYLE_ATTRIBUTE)
                 for match in pattern.finditer(text)]
        return re.sub(r"/\*.*?\*/", blank, keep_only(text, spans), flags=re.DOTALL)
    return ""


def script_code(path, text):
    """A script less its comments, or a page's inline scripts less theirs."""
    if is_script(path):
        return JS_COMMENT.sub(blank, text)
    if path.suffix == ".html":
        spans = [match.span(0) for match in re.finditer(
            r"(?<=>)[^<]*(?=</script\s*>)", text, re.IGNORECASE)]
        return JS_COMMENT.sub(blank, keep_only(text, spans))
    return ""


def js_strings(code):
    """A script's string literals, at their own positions, and nothing else of it."""
    return keep_only(code, [match.span() for match in JS_STRING.finditer(code)])


def page_text(text):
    """Markup's visible prose: no scripts, styles, code, comments or tags."""
    text = SCRIPT_OR_STYLE.sub(blank, text)
    text = CODE_BLOCK.sub(blank, text)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.DOTALL)
    return TAG.sub(blank, text)


def markup_of(path, text):
    """The HTML a file holds: a page less its scripts and styles, or a script's strings."""
    if path.suffix == ".html":
        return SCRIPT_OR_STYLE.sub(blank, text)
    return js_strings(script_code(path, text)) if is_script(path) else ""


def colours_of(value, variables, depth=0):
    """The (r, g, b) of each colour a CSS value names, through custom properties, and the
    names of any named colours in it."""
    found, names = [], set()
    for match in VAR_USE.finditer(value):
        if depth < 8 and match[1] in variables:
            more, more_names = colours_of(variables[match[1]], variables, depth + 1)
            found += more
            names |= more_names
    plain = VAR_USE.sub(" ", value)
    for match in HEX_COLOUR.finditer(plain):
        digits = match[1]
        if len(digits) in (3, 4):
            digits = "".join(char * 2 for char in digits[:3])
        found.append(tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4)))
    for match in RGB_COLOUR.finditer(plain):
        found.append(tuple(int(match[i]) for i in (1, 2, 3)))
    names |= {word.lower() for word in re.findall(r"[A-Za-z]+", plain)} & CREAM_NAMES
    return found, names


def cream_or_off_white(rgb):
    """Light enough to read as paper and not pure white: every channel at least 0xE6, or a
    warm light tint, red and green high and blue lower."""
    r, g, b = rgb
    if (r, g, b) == (255, 255, 255):
        return False
    return min(r, g, b) >= 0xE6 or (r >= 0xF0 and g >= 0xE0 and b >= 0xB0 and r >= b + 0x10)


def demo_background(root, files):
    """Every background a page sets, in CSS, a bgcolor attribute or a script, colours resolved
    through the custom properties of every file."""
    variables = {}
    for path in files:
        for match in CSS_VARIABLE.finditer(css_of(path, read_text(path) or "")):
            variables[match[1]] = match[2]
    violations = []
    for path in files:
        text = read_text(path) or ""
        css = css_of(path, text)
        settings = [(css, match.start(2), match[2]) for match in CSS_DECLARATION.finditer(css)
                    if match[1].lower() in ("background", "background-color")]
        code = script_code(path, text)
        settings += [(code, match.start(1), match[1]) for match in JS_COLOUR_SETTING.finditer(code)]
        if path.suffix == ".html":
            settings += [(text, match.start(1), match[1]) for match in HTML_BGCOLOR.finditer(text)]
        for source, at, value in settings:
            colours, names = colours_of(value, variables)
            light = [rgb for rgb in colours if cream_or_off_white(rgb)]
            if names or light:
                shown = sorted(names)[0] if names else "#%02x%02x%02x" % light[0]
                violations.append((path, line_of(source, at),
                                   f"cream or off-white background: {shown}"))
    return violations


def demo_italic_heading(root, files):
    """No italic anywhere a style can reach, and no tag that is italic by default inside a
    heading, in a page or in markup a script writes."""
    violations = []
    for path in files:
        text = read_text(path) or ""
        for scanned in (css_of(path, text), script_code(path, text)):
            for match in ITALIC.finditer(scanned):
                violations.append((path, line_of(scanned, match.start()),
                                   f"italic text: {match[0]}"))
        markup = markup_of(path, text)
        for heading in HEADING.finditer(markup):
            for tag in ITALIC_TAG.finditer(heading.group(2)):
                violations.append((path, line_of(markup, heading.start(2) + tag.start()),
                                   f"italic word in a heading: <{tag[1]}>"))
    return violations


def demo_numbered_label(root, files):
    """No leading-zero number standing alone as a label in a page's prose, in what a script
    writes, or in generated CSS content, and no leading-zero counter."""
    violations = []
    for path in files:
        text = read_text(path) or ""
        css = css_of(path, text)
        content = keep_only(css, [match.span(2) for match in CSS_DECLARATION.finditer(css)
                                  if match[1].lower() == "content"])
        for source in (css, script_code(path, text)):
            for match in LEADING_ZERO_COUNTER.finditer(source):
                violations.append((path, line_of(source, match.start()),
                                   "numbered label: decimal-leading-zero"))
        for source in (content, page_text(markup_of(path, text))):
            for match in NUMBERED_LABEL.finditer(source):
                violations.append((path, line_of(source, match.start()),
                                   f"numbered label: {match[0]}"))
    return violations


def demo_pill_button(root, files):
    """No corner rounded past MOST_RADIUS_PX, or by a share of the box, in CSS or a script."""
    violations = []
    for path in files:
        text = read_text(path) or ""
        for scanned in (css_of(path, text), script_code(path, text)):
            for match in BORDER_RADIUS.finditer(scanned):
                for value, unit in RADIUS_VALUE.findall(match[1]):
                    pixels = float(value) * {"rem": 16.0, "em": 16.0}.get(unit, 1.0)
                    if unit in ("%", "vh", "vw") or pixels > MOST_RADIUS_PX:
                        violations.append((path, line_of(scanned, match.start()),
                                           f"rounded enough to make a pill: {match[0].strip()}"))
                        break
    return violations


def demo_gradient(root, files):
    """No CSS gradient and no canvas gradient anywhere on the page."""
    violations = []
    for path in files:
        text = read_text(path) or ""
        scanned = JS_COMMENT.sub(blank, text) if is_script(path) else \
            re.sub(r"<!--.*?-->|/\*.*?\*/", blank, text, flags=re.DOTALL)
        for match in GRADIENT.finditer(scanned):
            violations.append((path, line_of(scanned, match.start()), f"gradient: {match[0]}"))
    return violations


def workflow_files(root):
    workflows = root / ".github" / "workflows"
    return sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])


def unpinned_action(root, files):
    violations = []
    for path in files:
        for number, line in enumerate((read_text(path) or "").splitlines(), start=1):
            match = WORKFLOW_USES.match(line)
            if match is not None and PINNED_ACTION.fullmatch(match[1].strip()) is None:
                violations.append((path, number, f"not a 40-hex SHA with its tag: {match[1]}"))
    return violations


def results_pages(root):
    return [path for path in walk(root, "results") if path.suffix in PROSE]


def linux_sampler_rule(root, files):
    """Every results file of every kind held to the Linux sampler rule, by check_results.py's
    own line and parser (check_linux_rule_any)."""
    # Here, so that the other gates need nothing beside them.
    import check_results  # noqa: PLC0415

    violations = []
    for path in files:
        text = read_text(path) or ""
        for code, detail in check_results.check_linux_rule_any(path.name, text):
            violations.append((path, 1, f"{code}: {detail}"))
    return violations


RUN_LOG = "results/run_log.md"


def run_log_file(root):
    path = root / RUN_LOG
    return [path] if path.is_file() else []


def run_log_kinds(root, files):
    """The run log's grammar (process_kinds.cause_breaks): each share a row's outcome gives, as
    N% or N percent, sits in a form the tools write, a process form whose subject is one of
    process_kinds.KINDS (with its time where a tool wrote it) or a fixed figure form; a share in
    no form fails, and so does a kind the sampler saw with no share. It holds the forms the tools
    write. It does not see a name with no share beside it, a share written another way, a name
    joined to a kind that has the share, or words before a figure form: process_kinds' docstring
    gives these limits."""
    import process_kinds  # noqa: PLC0415  Here, so that the other gates need nothing beside them.

    violations = []
    for path in files:
        for number, cause in process_kinds.run_log_rows(read_text(path) or ""):
            for why in process_kinds.cause_breaks(cause):
                violations.append((path, number, f"the outcome breaks the run log's grammar: "
                                                 f"{why}"))
    return violations


# r4-2: the SPSC paced and burst latencies are measured from the push, and none from the time
# each message was due. Each public page that gives or discusses them says so in a paragraph
# that also gives the reason: the due time is not carried in the item, so measuring from it
# is a change to measured code.
DUE_TIME_PAGES = ("BENCHMARKS.md", "RESULTS.md", "WRITEUP.md")
DUE_TIME_UNMEASURED = re.compile(r"\bno (?:latency|figure) from the due time (?:is|was) measured\b",
                                 re.IGNORECASE)


def due_time_pages(root):
    return [root / name for name in DUE_TIME_PAGES if (root / name).is_file()]


def spsc_due_time_unmeasured(root, files):
    violations = []
    for path in files:
        paragraphs = re.split(r"\n\s*\n", read_text(path) or "")
        if not any(DUE_TIME_UNMEASURED.search(p) and "measured code" in p for p in paragraphs):
            violations.append((path, 1, "no paragraph says that no SPSC latency from the due "
                                        "time is measured and that it needs a change to "
                                        "measured code (r4-2)"))
    return violations


@dataclass(frozen=True)
class Gate:
    name: str
    rule: str
    scan: object
    files: object


def gates():
    return (
        Gate("wire-pointer-cast", "no pointer cast to a multi-byte type over wire bytes, named, "
             "C-style or through an alias; it reads casts as written in include/, src/ and "
             "tools/, so one a called function makes elsewhere is not seen",
             wire_pointer_cast, lambda root: cxx_files(root, WIRE_CODE)),
        Gate("bare-branch-hint", "every branch hint or prefetch states a // measured: reason",
             bare_branch_hint, lambda root: cxx_files(root, ALL_CODE)),
        Gate("home-path", "no absolute path that names a user",
             scan_lines(HOME_PATH, "home path: {}"), lambda root: list(walk(root))),
        Gate("private-address", "no private or tailnet IP address",
             scan_lines(PRIVATE_ADDRESS, "private address: {}"), lambda root: list(walk(root))),
        Gate("private-name", "no result, flamegraph, code or script names this machine or its user",
             private_name, results_files),
        Gate("held-vendor", "the vendor held in docs/NOTES.md appears in no code, script or result",
             held_vendor, lambda root: list(walk(root))),
        Gate("no-conditional-compilation", "no hooks or test-only #if in parser code",
             preprocessor_condition, lambda root: cxx_files(root, PRODUCTION_CODE)),
        Gate("no-abort-on-wire", "no assert, throw or abort in parser code; it reads the calls "
             "it names, not types, so a standard container's .at(), which throws, is not seen",
             aborting_call, lambda root: cxx_files(root, PRODUCTION_CODE + REFERENCE_PARSERS)),
        Gate("toolchain-path", "no hardcoded toolchain path in build files or scripts",
             toolchain_path, lambda root: list(walk(root))),
        Gate("attribution-without-ledger",
             "the attribution and the headers it includes never include or name the ledger",
             reads_ledger, ledger_free_files),
        Gate("pinned-actions", "every action is pinned to a 40-hex SHA, with its tag in a comment",
             unpinned_action, workflow_files),
        Gate("demo-background", "the demo page has no cream or off-white background",
             demo_background, demo_files),
        Gate("demo-italic-heading", "the demo page has no italic text, and no italic word in a heading",
             demo_italic_heading, demo_files),
        Gate("demo-numbered-label", "the demo page has no numbered 01 / 02 / 03 labels",
             demo_numbered_label, demo_files),
        Gate("demo-pill-button", f"the demo page rounds no corner past {MOST_RADIUS_PX:g}px, so no "
             "button is a pill", demo_pill_button, demo_files),
        Gate("demo-gradient", "the demo page has no gradient, so no gradient hero",
             demo_gradient, demo_files),
        Gate("linux-sampler-rule", "every debian results file of any kind from the Linux "
             "sampler rule's moment on carries the rule's line, passing",
             linux_sampler_rule, results_pages),
        Gate("run-log-kinds", "every process a run-log row gives with its CPU share is given "
             "by one of process_kinds.KINDS, never by its program's name",
             run_log_kinds, run_log_file),
        Gate("spsc-due-time-unmeasured", "BENCHMARKS.md, RESULTS.md and WRITEUP.md each say "
             "that no SPSC latency from the due time is measured, and why",
             spsc_due_time_unmeasured, due_time_pages),
    )


def run_gate(gate, root):
    """Prints a gate's verdict and returns True if it passed."""
    files = gate.files(root)
    if not files:
        print(f"gates: FAIL {gate.name}: scanned no files")
        return False
    violations = gate.scan(root, files)
    for path, line, what in violations:
        print(f"gates: FAIL {gate.name} {relative(root, path)}:{line}: {what}")
    if violations:
        return False
    print(f"gates: ok {gate.name}: {len(files)} files, rule: {gate.rule}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--only", action="append", default=[], metavar="NAME")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    chosen = [gate for gate in gates() if not args.only or gate.name in args.only]
    if not chosen:
        print(f"gates: no gate named {args.only}", file=sys.stderr)
        return 2
    if args.list:
        for gate in chosen:
            print(f"{gate.name}\t{gate.rule}")
        return 0
    root = args.root.resolve()
    failed = [gate.name for gate in chosen if not run_gate(gate, root)]
    print(f"gates: {len(chosen) - len(failed)} of {len(chosen)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
