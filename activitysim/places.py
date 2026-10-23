"""Public pages the simulator is allowed to open.

Every built-in URL is https. Extra URLs from a file have to be http(s) with no
username or password. javascript:, file:, and other schemes are dropped.
"""

from __future__ import annotations

import random
import urllib.parse
from pathlib import Path

from activitysim.search import is_repo_url

REPOS: tuple[str, ...] = (
    "torvalds/linux",
    "python/cpython",
    "golang/go",
    "kubernetes/kubernetes",
    "microsoft/vscode",
    "facebook/react",
    "tensorflow/tensorflow",
    "rust-lang/rust",
    "django/django",
    "vercel/next.js",
    "neovim/neovim",
    "nodejs/node",
    "git/git",
    "apple/swift",
    "hashicorp/terraform",
    "prometheus/prometheus",
    "ansible/ansible",
    "home-assistant/core",
    "astral-sh/uv",
    "oven-sh/bun",
    "pallets/flask",
    "encode/httpx",
    "psf/requests",
    "numpy/numpy",
    "pandas-dev/pandas",
    "astral-sh/ruff",
    "tiangolo/fastapi",
    "pydantic/pydantic",
    "sqlalchemy/sqlalchemy",
    "Textualize/textual",
    "pytest-dev/pytest",
    "psf/black",
    "pallets/click",
    "aio-libs/aiohttp",
    "encode/starlette",
    "Kludex/uvicorn",
    "celery/celery",
    "redis/redis",
    "postgres/postgres",
    "sqlite/sqlite",
    "curl/curl",
    "openssl/openssl",
    "systemd/systemd",
    "tmux/tmux",
    "wez/wezterm",
    "ghostty-org/ghostty",
    "alacritty/alacritty",
    "kovidgoyal/kitty",
    "helix-editor/helix",
    "zed-industries/zed",
    "vim/vim",
    "emacs-mirror/emacs",
    "BurntSushi/ripgrep",
    "sharkdp/fd",
    "sharkdp/bat",
    "sharkdp/hyperfine",
    "dandavison/delta",
    "Wilfred/difftastic",
    "ajeetdsouza/zoxide",
    "bootandy/dust",
    "eza-community/eza",
    "junegunn/fzf",
    "jesseduffield/lazygit",
    "jesseduffield/lazydocker",
    "cli/cli",
    "starship/starship",
    "nushell/nushell",
    "fish-shell/fish-shell",
    "koalaman/shellcheck",
    "jqlang/jq",
    "mikefarah/yq",
    "go-task/task",
    "casey/just",
    "direnv/direnv",
    "denoland/deno",
    "evanw/esbuild",
    "vitejs/vite",
    "sveltejs/svelte",
    "vuejs/core",
    "angular/angular",
    "withastro/astro",
    "tailwindlabs/tailwindcss",
    "biomejs/biome",
    "prettier/prettier",
    "eslint/eslint",
    "vitest-dev/vitest",
    "microsoft/playwright",
    "cypress-io/cypress",
    "docker/compose",
    "containerd/containerd",
    "moby/moby",
    "helm/helm",
    "k3s-io/k3s",
    "derailed/k9s",
    "argoproj/argo-cd",
    "istio/istio",
    "envoyproxy/envoy",
    "caddyserver/caddy",
    "traefik/traefik",
    "etcd-io/etcd",
    "grpc/grpc",
    "grafana/grafana",
    "elastic/elasticsearch",
    "apache/kafka",
    "apache/spark",
    "apache/airflow",
    "duckdb/duckdb",
    "ClickHouse/ClickHouse",
    "minio/minio",
    "rclone/rclone",
    "restic/restic",
    "syncthing/syncthing",
    "hashicorp/vault",
    "hashicorp/consul",
    "opentofu/opentofu",
    "pulumi/pulumi",
    "aquasecurity/trivy",
    "goreleaser/goreleaser",
    "rust-lang/rust-analyzer",
    "microsoft/TypeScript",
    "llvm/llvm-project",
    "ziglang/zig",
    "NixOS/nix",
    "Homebrew/brew",
    "PowerShell/PowerShell",
    "microsoft/terminal",
    "coder/code-server",
    "huggingface/transformers",
    "pytorch/pytorch",
    "scikit-learn/scikit-learn",
    "scipy/scipy",
    "matplotlib/matplotlib",
    "streamlit/streamlit",
    "gradio-app/gradio",
    "vllm-project/vllm",
    "ollama/ollama",
    "ggml-org/llama.cpp",
    "BerriAI/litellm",
    "langchain-ai/langchain",
    "pypa/pip",
    "pyenv/pyenv",
    "nvm-sh/nvm",
    "pnpm/pnpm",
    "webpack/webpack",
    "honojs/hono",
    "fastify/fastify",
    "expressjs/express",
    "nestjs/nest",
    "trpc/trpc",
    "colinhacks/zod",
    "prisma/prisma",
    "yjs/yjs",
    "pola-rs/polars",
    "jupyterlab/jupyterlab",
    "semgrep/semgrep",
    "gitleaks/gitleaks",
    "rhysd/actionlint",
    "nektos/act",
    "supabase/supabase",
    "pocketbase/pocketbase",
    "keycloak/keycloak",
    "TryGhost/Ghost",
    "mastodon/mastodon",
    "mermaid-js/mermaid",
    "d3/d3",
    "leaflet/leaflet",
    "storybookjs/storybook",
    "remix-run/remix",
    "sveltejs/kit",
    "elixir-lang/elixir",
    "rails/rails",
    "JetBrains/kotlin",
    "dotnet/runtime",
    "gin-gonic/gin",
    "spf13/cobra",
    "charmbracelet/bubbletea",
    "jackc/pgx",
    "sqlc-dev/sqlc",
    "bufbuild/buf",
    "qdrant/qdrant",
    "weaviate/weaviate",
    "posthog/posthog",
    "dbt-labs/dbt-core",
    "dagster-io/dagster",
    "PrefectHQ/prefect",
    "apache/superset",
    "google/jax",
    "explosion/spaCy",
    "nltk/nltk",
    "jdx/mise",
    "asdf-vm/asdf",
    "volta-cli/volta",
    "Schniz/fnm",
    "microsoft/winget-cli",
    "ScoopInstaller/Scoop",
    "topgrade-rs/topgrade",
    "ClementTsang/bottom",
    "Canop/broot",
    "BurntSushi/xsv",
    "johnkerl/miller",
    "charmbracelet/gum",
    "sirupsen/logrus",
    "uber-go/zap",
    "fsnotify/fsnotify",
    "stretchr/testify",
    "go-chi/chi",
    "gofiber/fiber",
    "etcd-io/bbolt",
    "opencontainers/runc",
    "kubernetes-sigs/kind",
    "kubernetes-sigs/kustomize",
    "cilium/cilium",
    "coredns/coredns",
    "grafana/loki",
    "jaegertracing/jaeger",
    "fluxcd/flux2",
    "hashicorp/nomad",
    "saltstack/salt",
    "puppetlabs/puppet",
    "pre-commit/pre-commit",
    "nrwl/nx",
    "vercel/turborepo",
    "rollup/rollup",
    "parcel-bundler/parcel",
    "reduxjs/redux",
    "preactjs/preact",
    "solidjs/solid",
    "nuxt/nuxt",
    "apollographql/apollo-client",
    "graphql/graphql-js",
    "drizzle-team/drizzle-orm",
    "pgvector/pgvector",
    "milvus-io/milvus",
    "chroma-core/chroma",
    "run-llama/llama_index",
    "ml-explore/mlx",
    "pytorch/vision",
    "tobymao/sqlglot",
    "metabase/metabase",
    "plausible/analytics",
    "umami-software/umami",
    "ipython/ipython",
    "PyCQA/flake8",
    "PyCQA/isort",
    "evilmartians/lefthook",
    "lerna/lerna",
    "changesets/changesets",
    "rbenv/rbenv",
    "lsd-rs/lsd",
    "antonmedv/fx",
    "tomnomnom/gron",
    "itchyny/gojq",
    "pressly/goose",
    "amacneil/dbmate",
    "rs/zerolog",
    "labstack/echo",
    "gorilla/mux",
    "mattn/go-sqlite3",
    "go-sql-driver/mysql",
    "jmoiron/sqlx",
    "getkin/kin-openapi",
    "go-playground/validator",
    "creack/pty",
    "mattn/go-isatty",
    "rivo/tview",
    "gdamore/tcell",
    "muesli/termenv",
    "hyprwm/Hyprland",
    "swaywm/sway",
    "polybar/polybar",
    "Alexays/Waybar",
    "dunst-project/dunst",
    "keepassxreboot/keepassxc",
    "bitwarden/clients",
    "dani-garcia/vaultwarden",
    "paperless-ngx/paperless-ngx",
    "immich-app/immich",
    "jellyfin/jellyfin",
    "navidrome/navidrome",
    "photoprism/photoprism",
    "meilisearch/meilisearch",
    "typesense/typesense",
    "ankitects/anki",
    "obsidianmd/obsidian-releases",
    "logseq/logseq",
    "laurent22/joplin",
    "signalapp/Signal-Desktop",
    "element-hq/element-web",
    "matrix-org/synapse",
    "mattermost/mattermost",
    "outline/outline",
    "excalidraw/excalidraw",
    "tldraw/tldraw",
    "mui/material-ui",
    "chakra-ui/chakra-ui",
    "mantinedev/mantine",
    "radix-ui/primitives",
    "react-hook-form/react-hook-form",
    "TanStack/query",
    "vercel/swr",
    "phoenixframework/phoenix",
    "erlang/otp",
    "rubocop/rubocop",
    "scala/scala",
    "golangci/golangci-lint",
    "moby/buildkit",
    "ahmetb/kubectx",
    "stern/stern",
    "prometheus/alertmanager",
    "prometheus/node_exporter",
    "open-telemetry/opentelemetry-collector",
    "docker/cli",
    "compose-spec/compose-spec",
    "github/gitignore",
    "jestjs/jest",
    "mochajs/mocha",
    "avajs/ava",
    "sinonjs/sinon",
    "testing-library/react-testing-library",
    "webdriverio/webdriverio",
    "seleniumhq/selenium",
    "appium/appium",
)

# These paths exist on every GitHub repo page, including empty ones.
REPO_SUBPATHS: tuple[str, ...] = (
    "",
    "/issues",
    "/pulls",
    "/commits",
    "/releases",
    "/graphs/contributors",
)

TOPICS: tuple[str, ...] = (
    "python",
    "rust",
    "macos",
    "kubernetes",
    "typescript",
    "go",
    "machine-learning",
    "cli",
    "sqlite",
)

QUERIES: tuple[str, ...] = (
    "python asyncio tutorial",
    "git rebase explained",
    "how github actions cache works",
    "rust borrow checker examples",
    "kubernetes deployment vs statefulset",
    "sqlite wal mode",
    "macos accessibility permission terminal",
    "http caching headers",
    "regex lookaround examples",
    "cpython garbage collection",
)

WANDER: tuple[str, ...] = (
    "https://github.com/trending",
    "https://github.com/trending/python",
    "https://github.com/trending/rust",
    "https://github.com/explore",
    "https://news.ycombinator.com",
    "https://news.ycombinator.com/newest",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://docs.python.org/3/tutorial/index.html",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide",
    "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview",
)


def repo_name(url: str) -> str | None:
    """`owner/name` for a repository page, including its issues and commits."""

    if not is_repo_url(url):
        return None
    parts = [part for part in urllib.parse.urlsplit(url).path.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def safe_url(url: str) -> bool:
    if any(ch in url for ch in "\n\r\t "):
        return False
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False
    if not parts.netloc or parts.username or parts.password:
        return False
    return True


def load_url_file(path: Path) -> list[str]:
    found: list[str] = []
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if safe_url(line):
            found.append(line)
    return found


def _search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


class Places:
    """A short trail of related pages, then a new subject."""

    def __init__(self, rng: random.Random, extra_urls: list[str] | None = None) -> None:
        self.rng = rng
        self.extra = [url for url in (extra_urls or []) if safe_url(url)]
        self._queue: list[str] = []
        self.seen_repos: set[str] = set()
        self._repo_queue: list[str] = []
        self._planned: str | None = None
        self._reading: str | None = None
        self._last: str | None = None

    def plan_repo(self, name: str | None) -> None:
        """The repository a direct visit is about to open. Search clears this."""

        self._planned = name.lower() if name else None

    def leave_page(self) -> None:
        self._reading = None

    def saw_repo(self, url: str) -> bool:
        """Remember a repository page. True when this visit is a repeat."""

        name = repo_name(url)
        if not name:
            return False
        key = name.lower()
        if key == self._reading:
            return False
        repeated = key in self.seen_repos and key != self._planned
        self.seen_repos.add(key)
        self._reading = key
        self._repo_queue = [repo for repo in self._repo_queue if repo.lower() != key]
        return repeated

    def repo_burst(self) -> list[str]:
        """The repo root, then one or two of its tabs. Not a repo already seen."""

        repo = self._next_repo()
        self._planned = repo.lower()
        extras = list(REPO_SUBPATHS[1:])
        self.rng.shuffle(extras)
        paths = [""] + extras[: self.rng.randint(1, 2)]
        return [f"https://github.com/{repo}{path}" for path in paths]

    def _next_repo(self) -> str:
        for _ in range(2):
            while self._repo_queue:
                repo = self._repo_queue.pop()
                if repo.lower() not in self.seen_repos:
                    self.seen_repos.add(repo.lower())
                    self._last = repo.lower()
                    return repo
            self._refill_repos()
        repo = REPOS[0]
        self.seen_repos.add(repo.lower())
        self._last = repo.lower()
        return repo

    def _refill_repos(self) -> None:
        pool = [repo for repo in REPOS if repo.lower() not in self.seen_repos]
        if not pool:
            keep = self._last
            self.seen_repos.clear()
            if keep:
                self.seen_repos.add(keep)
            pool = [repo for repo in REPOS if repo.lower() not in self.seen_repos]
        if not pool:
            self.seen_repos.clear()
            pool = list(dict.fromkeys(REPOS))
        self.rng.shuffle(pool)
        self._repo_queue = pool

    def next(self) -> str:
        if not self._queue:
            self._queue = self._burst()
        url = self._queue.pop(0)
        if not safe_url(url):
            raise RuntimeError(f"refusing to open {url!r}")
        return url

    def _burst(self) -> list[str]:
        kinds = ["repo", "repo", "repo", "topic", "search", "wander"]
        if self.extra:
            kinds.append("extra")
        kind = self.rng.choice(kinds)
        if kind == "repo":
            return self._repo_burst()
        if kind == "topic":
            topic = self.rng.choice(TOPICS)
            return [f"https://github.com/topics/{topic}"]
        if kind == "search":
            return [_search_url(self.rng.choice(QUERIES))]
        if kind == "extra":
            return [self.rng.choice(self.extra)]
        return [self.rng.choice(WANDER)]

    def _repo_burst(self) -> list[str]:
        return self.repo_burst()


