"""Which model reads photos and phrases summaries: the built-ins, or a plug-in.

`[vision] provider` and `[summary] provider` take a built-in name, or
``package.module:factory`` naming a plug-in on the Python path. A factory is
called as ``factory(config, key)``, where ``key("SOME_API_KEY")`` gives that
key from the environment or `.env`, or None. It returns a provider, or None
when it cannot work (no key, say):

- a vision factory returns a `vision.base.VisionProvider`. It takes Claude's
  place as the cloud tier: the local Ollama model reads whenever it returns
  None, raises `base.DraftUnreadable`, or the budget is spent. It is asked
  for `vision_cloud_model` (or `_detail_model` for a closer look), and
  reports what each call cost as `last`, a `base.Reading`.
- a summary factory returns a `phrasing.Phraser`. None, or any exception
  from `phrase`, and the line is assembled from the contents instead.

The guide, with a worked example, is `docs/providers.md`. Built per use, as
the built-ins are, so a provider that comes back needs no restart; `check`
resolves every plug-in once at startup, so a typo stops the app rather than
quietly reading locally.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Callable
from typing import Any

from .config import ROOT, Config, ConfigError, is_spec

VISION_BUILT_IN = ("claude", "ollama", "stub")
SUMMARY_BUILT_IN = ("ollama",)

#: Searched before the environment: git-ignored, so an install's own plug-in
#: stays out of the repository, and `uv run` leaves it alone.
PLUGINS = ROOT / "plugins"

_SPEC = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


def load(spec: str, *, search: tuple = (PLUGINS,)) -> Callable[..., Any]:
    """The factory a ``package.module:factory`` spec names, or ConfigError."""
    if not _SPEC.match(spec):
        raise ConfigError(f"{spec!r} is not a plug-in: expected package.module:factory")
    for directory in search:
        if directory.is_dir() and str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    module_name, attribute = spec.split(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as failure:
        raise ConfigError(f"{spec}: cannot import {module_name} ({failure})") from failure
    try:
        factory = getattr(module, attribute)
    except AttributeError:
        raise ConfigError(f"{spec}: {module_name} has no {attribute}") from None
    if not callable(factory):
        raise ConfigError(f"{spec}: {attribute} is not callable")
    return factory


def check(config: Config) -> None:
    """Refuse, at startup, a provider setting that names nothing usable."""
    for setting, name, built_in in (
        ("[vision] provider", config.vision_provider, VISION_BUILT_IN),
        ("[summary] provider", config.summary_provider, SUMMARY_BUILT_IN),
    ):
        if is_spec(name):
            load(name)
        elif name not in built_in:
            choices = ", ".join(f'"{b}"' for b in built_in)
            raise ConfigError(
                f"{setting} is {name!r}: expected {choices} or package.module:factory"
            )


# --- photos -----------------------------------------------------------------


def cloud(config: Config):
    """The cloud tier this configuration offers photos to first, or None."""
    if config.vision_provider == "claude":
        from .vision import claude

        return claude.provider_for(
            config.anthropic_api_key, detail_model=config.vision_cloud_detail_model
        )
    if is_spec(config.vision_provider):
        return load(config.vision_provider)(config, config.key_lookup)
    return None


def vision(config: Config):
    """The configured vision provider, for routes and the worker.

    A cloud tier -- Claude or a plug-in -- is a pair with the local model
    behind it; with nothing to try first it is still a pair, and photos are
    read locally.
    """
    if config.vision_provider == "stub":
        from .vision.stub import StubProvider

        return StubProvider(config.vision_stub_seconds, config.vision_detail_model)

    from .vision.ollama import OllamaProvider

    local = OllamaProvider(config.ollama_url)
    if not config.cloud_tier:
        return local

    from .vision import hybrid

    return hybrid.Hybrid(cloud=cloud(config), local=local, fallbacks=config.vision_fallbacks())


# --- summaries --------------------------------------------------------------


def phraser(config: Config):
    """The "From contents" phraser, or None to assemble the line without a model.

    A Config built directly (every test) gives None, so the suite never reaches
    a model.
    """
    if not config.phrase_summaries:
        return None
    if config.vision_provider == "stub":
        from .phrasing import StubPhraser

        return StubPhraser()
    if is_spec(config.summary_provider):
        return load(config.summary_provider)(config, config.key_lookup)

    from .phrasing import OllamaPhraser

    return OllamaPhraser(config.ollama_url)
