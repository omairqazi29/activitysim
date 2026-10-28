"""Public frontier-benchmark pages: nav, tasks, traces, and Astra.

Every URL is https and typable on a US keyboard. The catalog is what a person
can open without an account. Order inside a visit is shuffled, and a task or
trace is not opened again until the others have been used.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from urllib.parse import urlsplit

from activitysim.input import can_type
from activitysim.places import safe_url

_DEEPSWE = "https://deepswe.datacurve.ai"
_PROGRAM = "https://programbench.com"

ALLOWED_HOSTS = frozenset({
    "github.com",
    "www.github.com",
    "deepswe.datacurve.ai",
    "www.swebench.com",
    "swebench.com",
    "www.tbench.ai",
    "hub.harborframework.com",
    "livecodebench.github.io",
    "swe-bench-live.github.io",
    "swesmith.com",
    "programbench.com",
    "mini-swe-agent.com",
})

_DEEPSWE_SLUGS: tuple[str, ...] = (
    "abs-module-cache-flags",
    "abs-stepped-slices",
    "actionlint-action-pinning-lint",
    "adaptix-name-mapping-aliases",
    "aiomonitor-task-snapshots-diff",
    "anko-default-function-arguments",
    "anko-typed-variable-bindings",
    "arcane-drift-detection-baselines",
    "arktype-json-schema-refs-dependencies",
    "awilix-async-container-initialization",
    "bandit-incremental-cache-control",
    "bandit-interprocedural-taint-checks",
    "bandit-structured-nosec-directives",
    "boa-hierarchical-evaluation-cancellation",
    "cattrs-partial-structuring-recovery",
    "clack-async-autocomplete-options",
    "claude-code-by-agents-recursive-delegation",
    "cliffy-config-file-parsing",
    "csstree-shorthand-expansion-compression",
    "dasel-html-document-format",
    "dateutil-rfc5545-timezone-interop",
    "drizzle-orm-window-function-builders",
    "dynamodb-toolbox-conditional-attribute-requirements",
    "dynamodb-toolbox-lazy-recursive-schemas",
    "effect-sse-httpapi-streaming",
    "eicrud-keyset-pagination-cursor",
    "etree-xml-diff-patch",
    "expr-try-catch-errors",
    "fastapi-deprecation-response-headers",
    "fastapi-implicit-head-options",
    "fd-deterministic-multi-key-sorting",
    "geo-shapeindex-serialization",
    "go-critic-doc-link-checker",
    "go-genai-streamed-function-args",
    "go-git-worktree-merge-conflicts",
    "goreleaser-retry-publish-auditing",
    "gql-incremental-graphql-delivery",
    "happy-dom-abort-pending-body-reads",
    "happy-dom-deterministic-intersectionobserver",
    "helm-array-merge-strategies",
    "helm-unified-manifest-stream",
    "httpx-deterministic-cookie-store",
    "httpx-multipart-response-parsing",
    "httpx-streaming-json-iteration",
    "igel-persist-feature-schema",
    "ink-grid-box-layout",
    "ipython-session-bundle-replay",
    "katex-multicolumn-array-spans",
    "kcp-go-multiplexed-kcp-streams",
    "kea-atomic-signal-selectors",
    "kgateway-consistent-hash-policy",
    "kombu-single-active-consumer-priority",
    "kombu-virtual-queue-dead-lettering",
    "koota-composite-trait-aspects",
    "koota-deferred-mutation-buffer",
    "koota-entity-snapshot-rollback",
    "koota-pair-relation-tracking",
    "koota-query-predicates",
    "kysely-window-grouping-helpers",
    "langchain-request-coalescing",
    "mashumaro-flattened-dataclass-fields",
    "meriyah-explicit-resource-declarations",
    "mnamer-daemon-watch-lifecycle",
    "mobly-grouped-test-barriers",
    "narwhals-rolling-window-suite",
    "numba-stencil-boundary-modes",
    "obsidian-linter-auto-table-of-contents",
    "obsidian-linter-link-format-conversion",
    "obsidian-linter-scoped-ignore-markers",
    "ofetch-per-origin-circuit-breaker",
    "onedump-dump-encryption-pipeline",
    "opa-rego-rule-profiling",
    "opa-template-string-reconstruction",
    "optique-conditional-option-dependencies",
    "oxvg-structural-selector-preservation",
    "participle-grammar-conflict-analysis",
    "pebble-durability-wait-apis",
    "pest-character-class-coalescing",
    "prometheus-transactional-reload-status",
    "prometheus-typed-label-sorting",
    "psd-tools-blend-range-api",
    "pwntools-tube-multiplexing",
    "python-statemachine-state-data-scoping",
    "query-persist-restored-query-state",
    "quill-shared-toolbar-focus",
    "returns-validated-error-accumulation",
    "scc-bounded-memory-spilling",
    "scriggo-method-declarations",
    "skrub-duration-encoding",
    "sql-formatter-bigquery-pipe-formatting",
    "sqlfmt-create-table-ddl-formatting",
    "sqlite-utils-safe-import-checkpoints",
    "superjson-error-stack-serialization",
    "task-task-graph-export",
    "tengo-callable-instance-isolation",
    "tengo-destructuring-bindings",
    "termenv-preserve-ansi-resets",
    "testem-bail-on-test-failure",
    "testem-per-launcher-reports",
    "textual-kitty-key-phases",
    "textual-richlog-follow-state",
    "tomlkit-toml-table-converters",
    "true-myth-iterable-collection-combinators",
    "ts-pattern-match-each",
    "updo-policy-alerting",
    "valibot-recursive-schema-composition",
    "vitest-duration-sharding",
    "vulture-persistent-analysis-cache",
    "wasmi-trap-coredumps",
    "wazero-multi-module-snapshots",
    "yaegi-go-embed-directives",
    "yjs-map-conflict-detection",
    "ytt-jsonpath-query-api",
)

# Trial ids as published on the task page. Some ids shorten the task slug.
_DEEPSWE_TRACES: dict[str, tuple[str, ...]] = {
    "abs-module-cache-flags": (
        "abs-module-cache-flags__26RXreW",
        "abs-module-cache-flags__2F3eHa3",
        "abs-module-cache-flags__2GnqRiz",
    ),
    "csstree-shorthand-expansion-compression": (
        "csstree-shorthand-expansion-comp__27A5ryp",
        "csstree-shorthand-expansion-comp__2CReM9M",
        "csstree-shorthand-expansion-comp__2SkUuAG",
    ),
    "gql-incremental-graphql-delivery": (
        "gql-incremental-graphql-delivery__2FxUe4b",
        "gql-incremental-graphql-delivery__2PC72nP",
        "gql-incremental-graphql-delivery__2bMuZk2",
    ),
    "happy-dom-abort-pending-body-reads": (
        "happy-dom-abort-pending-body-reads__34HDhKA",
    ),
    "koota-deferred-mutation-buffer": (
        "koota-deferred-mutation-buffer__22uMRf2",
        "koota-deferred-mutation-buffer__2Nu4dVs",
        "koota-deferred-mutation-buffer__2QJrohH",
    ),
    "opa-template-string-reconstruction": (
        "opa-template-string-reconstructi__2Bhqv7t",
        "opa-template-string-reconstructi__2UjmqdD",
        "opa-template-string-reconstructi__2VAksyf",
    ),
    "sqlfmt-create-table-ddl-formatting": (
        "sqlfmt-create-table-ddl-formatti__2CZCJyo",
        "sqlfmt-create-table-ddl-formatti__2EGczhX",
        "sqlfmt-create-table-ddl-formatti__2JDVVeX",
    ),
}

_PROGRAM_TASKS: tuple[str, ...] = (
    "jqlang__jq.b33a763/",
    "burntsushi__ripgrep.3b7fd44/",
    "sharkdp__fd.40d8eb3/",
    "sharkdp__bat.f822bd0/",
    "sharkdp__hyperfine.327d5f4/",
    "duckdb__duckdb.bdb65ec/",
    "junegunn__fzf.b56d614/",
    "jesseduffield__lazygit.1d0db51/",
    "tree-sitter__tree-sitter.5e23cca/",
    "typst__typst.88356d0/",
    "ffmpeg__ffmpeg.360a402/",
    "php__php-src.c891263/",
    "sqlite__sqlite.839433d/",
    "lua__lua.c6b4848/",
    "ninja-build__ninja.cc60300/",
    "htop-dev__htop.523600b/",
    "dandavison__delta.acd758f/",
    "ajeetdsouza__zoxide.67ca1bc/",
    "canop__broot.d6c798e/",
    "bootandy__dust.62bf1e1/",
    "go-critic__go-critic.9aea378/",
    "mikefarah__yq.602586d/",
    "johnkerl__miller.8d85b46/",
    "facebook__zstd.1168da0/",
    "google__brotli.b3dc9cc/",
    "lz4__lz4.1519f46/",
    "tukaani-project__xz.1007bf0/",
    "rust-lang__mdbook.37273ba/",
    "alecthomas__chroma.8d04def/",
    "ast-grep__ast-grep.dde0fe0/",
    "osgeo__gdal.0847f12/",
    "osgeo__proj.75d455c/",
    "gromacs__gromacs.665ea4c/",
    "samtools__samtools.aa823b5/",
)

ASTRA: tuple[str, ...] = (
    f"{_PROGRAM}/run/gpt-6-astra-max/",
    f"{_DEEPSWE}/data/v1.1/trials?model=gpt-6-astra",
)


def _trial(trial_id: str) -> str:
    return f"{_DEEPSWE}/data/v1.1/trials/{trial_id}"


TRACES_BY_TASK: dict[str, tuple[str, ...]] = {
    slug: tuple(_trial(trial_id) for trial_id in trial_ids)
    for slug, trial_ids in _DEEPSWE_TRACES.items()
}


@dataclass(frozen=True)
class Site:
    name: str
    home: str
    nav: tuple[str, ...]
    tasks: tuple[str, ...]
    traces: tuple[str, ...]


def _deepswe() -> Site:
    tasks = tuple(f"{_DEEPSWE}/data/v1.1/tasks/{slug}" for slug in _DEEPSWE_SLUGS)
    traces = tuple(url for group in TRACES_BY_TASK.values() for url in group)
    return Site(
        name="deepswe",
        home=f"{_DEEPSWE}/",
        nav=(
            f"{_DEEPSWE}/blog",
            f"{_DEEPSWE}/blog/deepswe",
            f"{_DEEPSWE}/blog/deepswe-v1-1",
            f"{_DEEPSWE}/run",
            f"{_DEEPSWE}/data/v1.1",
            f"{_DEEPSWE}/data/v1.1/tasks",
            f"{_DEEPSWE}/data/v1.1/trials",
            f"{_DEEPSWE}/changelog",
            "https://github.com/datacurve-ai/deep-swe",
        ),
        tasks=tasks,
        traces=traces,
    )


DEEPSWE = _deepswe()

SITES: tuple[Site, ...] = (
    DEEPSWE,
    Site(
        name="programbench",
        home=f"{_PROGRAM}/",
        nav=(
            f"{_PROGRAM}/tasks/",
            f"{_PROGRAM}/extended/",
            f"{_PROGRAM}/blog/",
            f"{_PROGRAM}/team/",
            "https://github.com/facebookresearch/ProgramBench",
        ),
        tasks=tuple(f"{_PROGRAM}/task/{name}" for name in _PROGRAM_TASKS),
        traces=(
            f"{_PROGRAM}/run/gpt-6-astra-max/",
            f"{_PROGRAM}/run/claude-opus-5-xhigh/",
            f"{_PROGRAM}/run/gemini-3-7-flash/",
            f"{_PROGRAM}/run/gpt-5-6-sol-xhigh/",
            f"{_PROGRAM}/run/muse-wonder-xhigh/",
        ),
    ),
    Site(
        name="swebench",
        home="https://www.swebench.com/",
        nav=(
            "https://www.swebench.com/verified.html",
            "https://www.swebench.com/lite.html",
            "https://www.swebench.com/multilingual.html",
            "https://www.swebench.com/multimodal.html",
            "https://www.swebench.com/original.html",
            "https://www.swebench.com/viewer.html",
            "https://www.swebench.com/blog.html",
            "https://github.com/swe-bench/SWE-bench",
        ),
        tasks=(
            "https://www.swebench.com/verified.html",
            "https://www.swebench.com/lite.html",
            "https://www.swebench.com/multilingual.html",
            "https://www.swebench.com/viewer.html",
        ),
        traces=(
            "https://mini-swe-agent.com/latest/usage/inspector/",
            "https://mini-swe-agent.com/latest/usage/swebench/",
        ),
    ),
    Site(
        name="terminal-bench",
        home="https://www.tbench.ai/",
        nav=(
            "https://www.tbench.ai/benchmarks",
            "https://www.tbench.ai/news",
            "https://www.tbench.ai/run",
            "https://www.tbench.ai/contributors",
            "https://github.com/harbor-framework/terminal-bench",
        ),
        tasks=(
            "https://www.tbench.ai/news/terminal-bench-4-0",
            "https://www.tbench.ai/news/writing-a-good-terminal-bench-task",
            "https://www.tbench.ai/news/terminal-bench-3-0",
            "https://www.tbench.ai/news/announcement-2-0",
            "https://hub.harborframework.com/datasets/terminal-bench/terminal-bench/4?tab=tasks",
            "https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2/latest",
        ),
        traces=(),
    ),
    Site(
        name="livecodebench",
        home="https://livecodebench.github.io/",
        nav=(
            "https://livecodebench.github.io/leaderboard.html",
            "https://livecodebench.github.io/leaderboard_v5.html",
            "https://livecodebench.github.io/gso.html",
            "https://github.com/LiveCodeBench/LiveCodeBench",
        ),
        tasks=(
            "https://livecodebench.github.io/leaderboard.html",
            "https://livecodebench.github.io/leaderboard_v5.html",
            "https://livecodebench.github.io/gso.html",
        ),
        traces=(),
    ),
    Site(
        name="swe-bench-live",
        home="https://swe-bench-live.github.io/",
        nav=(
            "https://github.com/microsoft/SWE-bench-Live",
            "https://www.swebench.com/",
        ),
        tasks=(),
        traces=(),
    ),
    Site(
        name="swesmith",
        home="https://swesmith.com/",
        nav=(
            "https://swesmith.com/getting_started",
            "https://github.com/SWE-bench/SWE-smith",
            "https://www.swebench.com/",
        ),
        tasks=("https://swesmith.com/getting_started",),
        traces=(),
    ),
)


def allowed_url(url: str) -> bool:
    if not safe_url(url) or not can_type(url):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return host in ALLOWED_HOSTS


def page_label(url: str) -> str:
    """What a click on this page is: the nav, a task, or a trace."""

    path = urlsplit(url).path.lower()
    if "/trials/" in path or "/run/" in path or path.rstrip("/").endswith("/inspector"):
        return "trace"
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    if ("/tasks/" in path or "/task/" in path) and tail not in {"tasks", "task"}:
        return "task"
    return "nav"


class Catalog:
    """One benchmark at a time, then a different one. Nav order is shuffled."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.seen: set[str] = set()
        self._order: list[Site] = []
        self._visits = 0

    def tour(self, site: Site | None = None) -> list[str]:
        chosen = site if site is not None else self._next_site()
        self._visits += 1
        urls: list[str] = [chosen.home]
        urls.extend(self._pick(chosen.nav, 2, 4))
        task_urls = self._pick(chosen.tasks, 2, 3)
        saw_trace = False
        for task_url in task_urls:
            urls.append(task_url)
            trace = self._trace_for(task_url)
            if trace:
                urls.append(trace)
                saw_trace = True
        if not saw_trace:
            urls.extend(self._pick(chosen.traces, 1, 1))
        if self._visits % 2 == 0:
            urls.extend(self._pick(ASTRA, 1, 1))
        return self._keep(urls)

    def _next_site(self) -> Site:
        if not self._order:
            self._order = list(SITES)
            self.rng.shuffle(self._order)
        return self._order.pop()

    def _pick(self, pool: tuple[str, ...], low: int, high: int) -> list[str]:
        if not pool or high < 1:
            return []
        fresh = [url for url in pool if url not in self.seen]
        if len(fresh) < low:
            fresh = list(pool)
        if not fresh:
            return []
        self.rng.shuffle(fresh)
        top = min(high, len(fresh))
        bottom = min(low, top)
        count = self.rng.randint(bottom, top)
        return fresh[:count]

    def _trace_for(self, task_url: str) -> str | None:
        slug = urlsplit(task_url).path.rstrip("/").rsplit("/", 1)[-1]
        options = TRACES_BY_TASK.get(slug, ())
        if not options:
            return None
        fresh = [url for url in options if url not in self.seen]
        return self.rng.choice(fresh or list(options))

    def _keep(self, urls: list[str]) -> list[str]:
        kept: list[str] = []
        for url in urls:
            if url in kept or not allowed_url(url):
                continue
            kept.append(url)
            self.seen.add(url)
        return kept
