# Provider plug-ins

Photos are read by Claude with a local Ollama model behind it, and summaries
are phrased by Ollama. To use anything else (OpenAI, Gemini, a model behind
your own server), write a plug-in: a Python function that builds a provider.
Nothing in the app needs changing.

```toml
[vision]
provider = "my_openai:vision"        # package.module:factory
cloud_model = "your-model-name"      # what the plug-in is asked for
cloud_detail_model = "your-larger-model"   # "Look closer"

[summary]
provider = "my_openai:phraser"
model = "your-small-model"
```

## Where the module goes

Put it in **`plugins/`** in the checkout (git ignores that directory), for
example `plugins/my_openai.py`. The app searches `plugins/` first, then its
own environment, so an installed package works too. The app **imports every
plug-in at startup**, and a module that will not import, a missing function or
a misspelt built-in name stops the app with its name, instead of every photo
quietly being read locally.

## The factory

```python
def vision(config, key):
    """Return a provider, or None when this one cannot work."""
    api_key = key("OPENAI_API_KEY")
    return OpenAIReader(api_key) if api_key else None
```

- `config` is the app's `movingbox.config.Config`: `vision_cloud_model`,
  `summary_model`, `ollama_url` and so on.
- `key(name)` gives that variable from the environment, else from the
  checkout's `.env` (the file `MOVING_ENV_FILE` names, if set), else `None`.
  Keys never go in `moving.toml`.
- **Return `None` when there is no key.** That is a working setup: photos are
  read locally, and Settings says the provider has no key.

The app builds the provider again for each use, so keep the factory cheap and
free of network calls.

## A vision provider

It takes Claude's place as the **cloud tier**, so it gets everything Claude
has: the local model reads whenever it cannot, the spending cap
(`[vision] budget_usd`) withdraws it, and Settings says so when it has
stopped answering.

```python
from movingbox.vision import base

class OpenAIReader:
    name = "openai"          # recorded on each job; "Read by openai" in the viewer

    def __init__(self, api_key):
        self.api_key = api_key
        self.last = None     # a base.Reading after each call

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        ...
```

`draft` is called with:

- `images`: the photos, as JPEG bytes the app keeps, **2048 px on the short
  edge and up to 4096 on the long**. Resize them to whatever your API reads
  best. `movingbox.vision.ollama.for_local(data)` gives a JPEG 2048 px on the
  long edge, which is enough for reading labels on things, and smaller files
  cost less.
- `model`: `cloud_model`, or `cloud_detail_model` for "Look closer".

Send `base.SYSTEM` as the system prompt and `base.INSTRUCTION` with the
images. Ask for replies in `base.SCHEMA` if the API can require that shape,
and turn each reply's text into a draft with **`base.parse(text)`**. It
forgives fences and chatter, and raises `base.DraftUnreadable` when there is
no usable draft.

It must:

- **Raise `base.DraftUnreadable`** for every failure: refused, rate limited,
  unreachable, timed out, unreadable. Any of these makes the local model read
  the photo instead. Any other exception fails the job outright, and the
  local model never gets a turn.
- **Set `self.last`** to a `base.Reading(provider=self.name, model=model,
  input_tokens=..., output_tokens=..., cost_usd=...)` for every call that was
  billed, *including one that then failed*, before raising. The cap is the sum
  of `cost_usd` over every job, so a provider that reports `0.0` is never
  capped.
- **Keep the key out of every message.** Whatever the provider raises is
  logged and stored on the job. Do not quote the request, or any reply that
  quotes the key back. **Send the key in a header, never in the URL**: an
  HTTP client's errors quote the URL, and Gemini's API accepts the key as
  `?key=` as well as in its `x-goog-api-key` header.
- **Time out.** The worker reads one photo at a time, and a call that hangs
  holds up every photo after it. Claude's is 60 seconds.

## A summary phraser

```python
from movingbox import phrasing

class OpenAIPhraser:
    name = "openai"

    def __init__(self, api_key):
        self.api_key = api_key

    def phrase(self, contents, *, model: str) -> str:
        system, user = phrasing.prompt(contents)
        text = ...            # send both, temperature 0, asking for phrasing.SCHEMA
        return phrasing.finish(text)

def phraser(config, key):
    api_key = key("OPENAI_API_KEY")
    return OpenAIPhraser(api_key) if api_key else None
```

- `phrasing.prompt(contents)` gives the system prompt and the user message,
  the same ones Ollama gets. `phrasing.finish(text)` pulls the line out of the
  reply and tidies it to label length. It raises `phrasing.Unusable` when the
  reply has no line.
- **Use temperature 0.** That is what makes pressing the button twice give the
  same line (measured: 7 records of 7, against 5 of 7 with sampling).
- Any exception from `phrase`, or taking longer than a person will wait,
  leaves the line assembled from the contents instead. Keep your own timeout
  short: Ollama's is 5 seconds.
- Summary calls are **not** counted against the photo budget, which is summed
  from photo jobs only. A phrase is one short text request.
- The app's keep-warm pings go to Ollama only, so a plug-in phraser is never
  warmed.

`[vision] provider = "stub"` still stubs phrasing too, for UI work.

## Testing one

No test in this repository may reach a network, and yours should not need to.
`tests/fake_providers.py` is a complete plug-in of both kinds with a canned
"API", and `tests/test_provider_hook.py` shows what the app does with one. To
try a real plug-in end to end, start a throwaway server with its settings and
upload a photo. That uses your key the way a person using the app would.
