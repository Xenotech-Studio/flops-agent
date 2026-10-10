# Run a task in five minutes

**Reader question:** Can I check that the framework works on my machine without creating a model account?

**Prerequisites:** Read [the framework boundaries](README.md). Have Python 3.10 or later and Git installed. **Outcome:** Run the offline example and observe a completed tool call and a memory record.

## Try it

For a first installation, clone the public repository. If you already have a local clone, start by entering its directory. These commands use a Unix-like shell; on Windows, use the virtual environment's `python.exe` for the same Python commands.

```sh
git clone https://github.com/Xenotech-Studio/flops-agent.git
cd flops-agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[providers]'
```

Installation needs network access to download dependencies. The example itself uses a fixed script to simulate a model: it needs no API key and makes no real model request. Use this entry point to prevent an existing environment variable from accidentally enabling the placeholder network endpoint:

```sh
python -c 'import os, runpy; os.environ.pop("EXAMPLE_PROVIDER_API_KEY", None); runpy.run_path("docs/sample_product/server.py", run_name="__main__")'
```

This installs your local clone. Checking out a version tag or editing the code changes what runs. This guide only depends on interfaces already present in v0.2.0.

## Check the result

The output should contain two example titles: `Safe tool (scripted)` and `Persona and memory (scripted)`. The first simulates a model requesting `run_command`; the executor returns text including `$ ls`, `line-1`, and `(done)`. The model then emits `The directory has been listed.` At the end, a memory record should contain `User said: My name is Ming`.

This demonstrates that model output was consumed, a tool result returned to the execution flow, and post-run memory maintenance was called. The example executor generates fixed text: **it does not actually execute `ls`**. Do not use a real directory listing as your success criterion.

If you see no result, check your working directory and virtual environment first. `python -c 'import flops_agent; print(flops_agent.__file__)'` should point to the package you just installed. If an import fails, repeat the installation step before configuring a real model.

## Boundaries

The command exits when it finishes. It does not listen on a port or start a browser, remote executor, or database. The in-memory example does not retain conversations after exit. Those are later integration tasks, not prerequisites for this first success.

A product process normally assembles Runtime at startup and shares it across requests. Do not recreate Runtime for every streaming connection: a new instance cannot find the previous instance's local run handles.

## Next steps

Read [Understand how a task runs](02-core-concepts.md), then [Connect your own model](connect-model.md). The service-entry material previously on this page is now in [subscriptions and reconnection](03-streaming-and-sse.md) and [product integration](08-worked-example.md).

## Code evidence

- `docs/sample_product/server.py:108`: deterministic model double; `:69`: simulated executor.
- `docs/sample_product/server.py:227`: entry point and the two demonstrations.
- `src/flops_agent/engine/runtime.py:1041`: Runtime creates and retains run handles.
