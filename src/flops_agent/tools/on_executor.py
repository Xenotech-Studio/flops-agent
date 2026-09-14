"""The framework's built-in executor tool package ``/tools/on_executor`` -- the base set of
capabilities for tools that run on the user's machine.

Tool **names and schemas are a contract** (the server builds the request, the executor
dispatches by name, and the two live in separate repos), so this module only holds
definitions: there are no handlers, everything is registered with ``route=ROUTE_EXECUTOR``, and
dispatch delivers to the executor via the product layer's injected ``ToolRouter`` (see the
protocol in ``flops_agent.executor``). The executor must implement tools with the same names;
see ``docs/sample_product/local_executor.py`` for a minimal example.

Two packages:

* ``/tools/on_executor``: a group package carrying no tools itself -- the entry point and index
  for the various executor sub-packages;
* ``/tools/on_executor/basic``: this module's nine tools -- run commands (including background
  mode), list directories, full-text search, read / write / exact-replace files, and list /
  stop / wait for background tasks.

The product layer can append its own tools under the same path
(``register_tool("/tools/on_executor/basic", …, route=ROUTE_EXECUTOR)``) or attach sibling
sub-packages (``/tools/on_executor/<its own package>``) without needing to modify this file.
Package copy: if the product layer registered these two packages **first** with its own copy,
this module does not override it; packages not yet registered fall back to the framework's
default copy.

Each tool's ``requires`` is a **capability tag the executor self-reports when it checks in
(auth)** (``executor.files.read``, etc.); once the product layer merges an online executor's
capability set into ``Runner.available_capabilities()``, tools missing a capability
automatically become invisible; the package-level ``requires`` defaults to ``executor:online``,
a product-layer-defined tag meaning "an executor is online".
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, cast

from .registry import DEFAULT_REGISTRY, ROUTE_EXECUTOR, ToolRegistry

PACKAGE_PATH = "/tools/on_executor"
BASIC_PATH = "/tools/on_executor/basic"

DEFAULT_GROUP_NAME = "Executor Local Capabilities"
DEFAULT_GROUP_DESCRIPTION = (
    "The overall entry point for local capabilities on the user's connected device. Opening this "
    "package exposes the various executor sub-packages (basic: read/write local files, list "
    "directories, run commands). Requires an online executor."
)
DEFAULT_GROUP_PROMPT = """## Executor Local Capabilities (opened)

These are the capabilities of the user's **local device itself**. Sub-package tools are not
available just because this package is open -- request that the corresponding sub-package be
opened first, then call its tools. `/tools/on_executor/basic`:
read/write local files, list directories, full-text search, run one-off commands (including a
background long-running mode), and background task management."""

DEFAULT_BASIC_NAME = "Executor Basic Tools Package"
DEFAULT_BASIC_DESCRIPTION = (
    "On a connected executor: read / write / exact-replace files, list directories, full-text "
    "search, run commands (including background mode: for processes that don't naturally "
    "terminate such as dev servers or watchers, use the background parameter; a "
    "[Background Task Completed] message is sent back when the process ends), and list / stop / "
    "wait for background tasks. Requires an online executor and this package to be opened."
)
DEFAULT_BASIC_PROMPT = """## Executor Basic Tools Package (opened)

- Path parameters are relative to the workspace root or absolute; file tools accept either path
  or file_path (either one).
- local_read_file supports offset/limit paging for continued reads; local_edit_file does exact
  replacement -- old_string must be unique within the file, otherwise it errors and reports the
  number of matches, at which point add more surrounding context or pass replace_all=true.
- For commands that don't naturally terminate (dev server, watcher, tail -f), use
  `local_exec_command(background=true)`, which immediately returns {task_id, pid, log_path};
  read log_path with local_read_file to check output midway, use local_task_stop to stop it, and
  local_task_wait to wait explicitly. Don't use background mode for short one-off commands.
- local_exec_command's stdin is closed and it doesn't retain state across commands; every call is
  a fresh process.
- When multiple devices are online, pass device_id in the call to target a specific device."""


BASIC_TOOLS: List[Dict[str, Any]] = [{'type': 'function',
  'function': {'name': 'local_exec_command',
               'description': 'Execute a shell command on the local executor. Suitable for git clone, installing dependencies, running '
                              'build/test commands. Returns exit_code/stdout/stderr. When calling, output the description field (purpose '
                              'description) first, then command and the other fields, so the UI can stream it. Optionally pass device_id to '
                              'pick which device runs it. **To run a command inside a conda/venv environment, pass one of '
                              '`conda_env`/`conda_prefix`/`venv` -- do not write `conda run` / `conda activate` into `command`** -- the '
                              'server wraps it for you (see the basic tools package docs for how the three are chosen between, '
                              '`LD_LIBRARY_PATH` injection, etc.). **`tmp/` is exempt from review**: if `cwd` is set to the workspace-relative '
                              'path `tmp/` or a subpath under `tmp/`, sensitive operations like `rm`, `mv`, etc. are auto-approved without '
                              'waiting for a user confirmation prompt -- suitable for build-artifact cleanup, temp-file operations, etc. '
                              '**Transparent WSL2 execution**: when the executor has the `executor.command.wsl` capability (Windows only, '
                              'with a WSL distro installed), you can pass `use_wsl=true` to send the command into the default WSL2 '
                              'distribution\'s bash instead of native PowerShell; suitable for cases where Linux tools like grep/sed/awk/curl '
                              'are more convenient; Unix executors ignore this field. **Background mode**: passing `background=true` returns '
                              'immediately with `{task_id, pid, log_path}`, and the process runs independently detached from the parent '
                              'conversation (stdout/stderr merged into the log file); suitable for long-running tasks that don\'t naturally '
                              'terminate such as visualization processes, dev servers, watchers -- **do not** use background mode for '
                              'short one-off commands. When a background task process ends, its status is automatically reported to you via '
                              'a system-marked message; use `local_read_file` to read log_path if you want to check output midway; use '
                              '`local_task_stop(task_id)` to stop it; use `local_task_list` to see all currently running background tasks on '
                              'this machine. timeout_seconds has no effect in background mode. **This tool\'s stdin is closed**: it cannot run '
                              'any program that requires input (interactive prompts, passwords, REPLs, ssh sessions all fail outright or hang '
                              'until timeout), **and it also retains no state across commands** (every call is a fresh process -- the '
                              'previous command\'s `cd` / `export` / `conda activate` is gone by the next one, so the environment must be set '
                              'via the conda_env/venv parameters rather than typing activate yourself). For these two needs, use '
                              '`local_terminal_open` to open a real interactive terminal instead.',
               'parameters': {'type': 'object',
                              'properties': {'description': {'type': 'string',
                                                             'description': 'An extremely short purpose description of this command (a few words), for UI display, e.g.: list directory, install dependencies, run tests. Prefer outputting this field first. If omitted, the UI displays "Terminal Command".'},
                                             'command': {'type': 'string',
                                                         'description': 'The command string, e.g. git clone https://...'},
                                             'cwd': {'type': 'string', 'description': 'Optional. Working directory (relative to the local workspace root, or absolute)'},
                                             'timeout_seconds': {'type': 'integer',
                                                                 'description': 'Optional. Maximum run time in seconds for the command, capped at 86400 (24h). 180 is recommended for short tasks; 3600 for longer builds/downloads. If the model omits this field, the system defaults to 3600. This parameter is ignored when background=true.'},
                                             'background': {'type': 'boolean',
                                                            'description': 'Optional. true means enable background mode: the process runs '
                                                                           'independently detached from the conversation, the tool returns '
                                                                           'immediately with {task_id, pid, log_path}, and a system-marked '
                                                                           'notification is sent automatically when it ends. Use only for '
                                                                           '**commands expected to run long and that don\'t need stdout '
                                                                           'immediately** (dev server, visualization process, watcher, '
                                                                           'etc.). Do not enable it for short commands or ones where you '
                                                                           'need to see the output to decide the next step right away.'},
                                             'use_wsl': {'type': 'boolean',
                                                         'description': 'Optional, defaults to false. Only takes effect when the target '
                                                                        'executor has the `executor.command.wsl` capability (Windows with a '
                                                                        'WSL2 distro installed): when true, the command is transparently '
                                                                        'sent into the default WSL2 distribution\'s bash instead of native '
                                                                        'PowerShell; suitable for commands like grep/sed/awk/curl/find that '
                                                                        'work more smoothly on Linux, or scripts/toolchains that need a '
                                                                        'Linux subsystem. Unix executors ignore this field. Note: when '
                                                                        'use_wsl=true the command should use bash syntax and Linux-style '
                                                                        'paths (PowerShell syntax will not work).'},
                                             'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID (device_id). If omitted, the device bound to the current session is used.'},
                                             'conda_env': {'type': 'string',
                                                           'description': 'The conda environment **name** (e.g. `torch2`, must be located '
                                                                          'under conda\'s default envs_dirs). Mutually exclusive with '
                                                                          'conda_prefix/venv; see the basic tools package docs for usage details.'},
                                             'conda_prefix': {'type': 'string',
                                                              'description': 'The **absolute path** to the conda environment directory '
                                                                             '(use this when the environment isn\'t under the default '
                                                                             'envs_dirs and can\'t be found by name). Mutually exclusive '
                                                                             'with conda_env/venv; see the basic tools package docs for usage details.'},
                                             'venv': {'type': 'string',
                                                      'description': 'Path to the Python venv directory. Mutually exclusive with conda_env/conda_prefix; see the basic tools package docs for usage details.'}},
                              'required': ['command']}}},
 {'type': 'function',
  'function': {'name': 'local_list_files',
               'description': 'List the contents of a local workspace directory, suitable for inspecting project structure.',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID (device_id). If omitted, the device bound to the current session is used.'},
                                             'path': {'type': 'string', 'description': 'Directory path (relative to the workspace root, or absolute)'},
                                             'max_depth': {'type': 'integer', 'description': 'Optional. Recursion depth, defaults to 2'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'local_search_files',
               'description': 'Full-text search within a local executor\'s directory tree (equivalent to grep -rn, pure Python engine). '
                              'Matches by **literal substring** and is case-insensitive by default; when is_regex=true, query is '
                              'interpreted as Python re syntax (an invalid regex returns ok:false + error without breaking the '
                              'conversation). By default it skips dependency/build-artifact directories such as '
                              '.git/node_modules/__pycache__/.venv/dist/build/release/target and lockfiles/minified artifacts such as '
                              'package-lock.json/*.min.js (can be overridden with exclude_patterns, pass [] to exclude nothing), as well as '
                              'non-text file extensions and files larger than 1.5MB; include_patterns can restrict the scope to certain '
                              'files/directories (e.g. ["src/", "*.py"]). Returns at most 200 matching lines with a total time budget of 10 '
                              'seconds; truncated=true in the result means it was cut off, in which case narrow the path scope or use a more '
                              'precise keyword and search again. Returns files:[{path (relative to the start directory), '
                              'matches:[{line,col,text}]}] + total_matches/files_scanned. Prefer this tool over running grep via '
                              'local_exec_command when looking for "where is this piece of code".',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID (device_id). If omitted, the device bound to the current session is used.'},
                                             'query': {'type': 'string',
                                                       'description': 'Search content. Matches by literal substring by default; interpreted as a Python regex when is_regex=true'},
                                             'path': {'type': 'string',
                                                      'description': 'Optional. Start directory (relative to the workspace root, or absolute), defaults to the workspace root. Pass a subdirectory when the project location is known, for speed and less noise'},
                                             'is_regex': {'type': 'boolean',
                                                          'description': 'Optional, defaults to false (literal match). When true, query is '
                                                                         'interpreted using Python re syntax'},
                                             'case_sensitive': {'type': 'boolean',
                                                                'description': 'Optional, defaults to false (case-insensitive)'},
                                             'max_results': {'type': 'integer',
                                                             'description': 'Optional. Maximum number of matching lines returned; both the default and the hard cap are 200'},
                                             'include_patterns': {'type': 'array',
                                                                  'items': {'type': 'string'},
                                                                  'description': 'Optional. Only search files matching these rules (files '
                                                                                 'to include); same syntax as exclude_patterns (a subset '
                                                                                 'of gitignore syntax), semantically the dual: src/ means '
                                                                                 'all files under any src/ directory at any depth; *.py '
                                                                                 'means all .py files; /dist, dist/assets/ means files '
                                                                                 'under that path relative to the start directory. Not '
                                                                                 'passing this, or passing an empty list, means no '
                                                                                 'restriction. Takes effect together with '
                                                                                 'exclude_patterns: result = matches include AND does not match exclude'},
                                             'exclude_patterns': {'type': 'array',
                                                                  'items': {'type': 'string'},
                                                                  'description': 'Optional. List of exclusion rules, a subset of gitignore '
                                                                                 'syntax: a plain directory name prunes exactly that name at '
                                                                                 'any depth (node_modules); a name glob matches both files '
                                                                                 'and directories (*.lock, test_*); a trailing / matches '
                                                                                 'directories only (build/, test_*/); containing / anchors '
                                                                                 'the path under the start directory (/dist, dist/assets). '
                                                                                 '! negation and ** are not supported. If not passed, the '
                                                                                 'default exclusion list is used (directories like '
                                                                                 '.git/node_modules plus files like '
                                                                                 'package-lock.json/*.min.js); if explicitly passed, it is '
                                                                                 'used exactly as given, and passing [] means excluding nothing'},
                                             'exclude_dirs': {'type': 'array',
                                                              'items': {'type': 'string'},
                                                              'description': 'Legacy, new code should use exclude_patterns. A list of '
                                                                             'directory names to exclude (pruned by exact name match, at '
                                                                             'any depth); if explicitly passed it takes precedence over exclude_patterns'}},
                              'required': ['query']}}},
 {'type': 'function',
  'function': {'name': 'local_read_file',
               'description': 'Read local file contents, **including images** -- this tool is the agent\'s entry point for looking at images.\n'
                              '\n'
                              '[Text] .md/.py/.json/.txt/.log/source code/config files etc.: returns a content string; large files support offset/limit paging for continued reads.\n'
                              '\n'
                              '[Images] .png/.jpg/.jpeg/.gif/.webp: **just call it once, and you\'ll be able to see the image content next turn** (screenshots, photos, diagrams, UI mocks all work).\n'
                              'Strictly avoid the following wrong moves:\n'
                              '  ✗ Telling the user "I can\'t view images directly" / "I\'m a text-only model" / "I have no vision tool" -- you **do** have one, this is it;\n'
                              '  ✗ Asking the user to "post the image in chat" or "upload it to you" -- if they gave you a path, they want you to read it yourself;\n'
                              '  ✗ Falling back to metadata commands like sips/file/exiftool/identify/`open` as a substitute;\n'
                              '  ✗ Using local_file_to_attachment_url to return the user a download link as a "response";\n'
                              '  ✗ Guessing in your thinking that "this tool will probably return binary garbage" -- it won\'t, **just call it first**.\n'
                              'The server automatically feeds the image visually to the next LLM call after this turn\'s tool_result; at that point just describe/analyze what you see directly.\n'
                              '\n'
                              'Parameters: path or file_path (either one, relative to the workspace or absolute).',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID (device_id). If omitted, the device bound to the current session is used.'},
                                             'path': {'type': 'string', 'description': 'File path (relative to the workspace root, or absolute)'},
                                             'file_path': {'type': 'string', 'description': 'Synonym for path, either one works'},
                                             'max_bytes': {'type': 'integer', 'description': 'Optional. Maximum bytes to read, defaults to 200000'},
                                             'offset': {'type': 'integer',
                                                        'description': 'Optional. Line number to start reading from (1-based), used for paging through large files'},
                                             'limit': {'type': 'integer', 'description': 'Optional. Maximum number of lines returned per call, used to control the length of a single read'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'local_write_file',
               'description': 'Create or overwrite an entire file in the local workspace. Use path or file_path for the location (either one); content is the full content to write.',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID (device_id). If omitted, the device bound to the current session is used.'},
                                             'path': {'type': 'string', 'description': 'File path (relative to the workspace root, or absolute)'},
                                             'file_path': {'type': 'string', 'description': 'Synonym for path, either one works'},
                                             'content': {'type': 'string', 'description': 'The complete file content to write (overwrites the whole file)'}},
                              'required': ['content']}}},
 {'type': 'function',
  'function': {'name': 'local_edit_file',
               'description': 'Make an exact replacement in a local file: find a span of text that exactly matches old_string/oldText and '
                              'replace it with new_string/newText. For small, targeted edits. Use path or file_path for the location; old and '
                              'new can be given as old_string/new_string or oldText/newText. new can be empty to delete that span. '
                              '**old_string must be unique within the file** -- if it occurs more than once, this errors out directly '
                              '(reporting the number of matches); in that case add more surrounding context to old_string to make it unique, '
                              'or pass replace_all=true to replace all occurrences. new_string must not be identical to old_string. A '
                              'successful response includes replaced_count / match_lines (matched line numbers) / diff (unified diff), which '
                              'can be used to confirm the change landed where expected.',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID (device_id). If omitted, the device bound to the current session is used.'},
                                             'path': {'type': 'string', 'description': 'File path (relative to the workspace root, or absolute)'},
                                             'file_path': {'type': 'string', 'description': 'Synonym for path, either one works'},
                                             'old_string': {'type': 'string',
                                                            'description': 'The exact original text to replace (must exactly match a contiguous span in the file, and be unique within the file; if not unique this errors)'},
                                             'oldText': {'type': 'string', 'description': 'Synonym for old_string, either one works'},
                                             'new_string': {'type': 'string',
                                                            'description': 'The replacement content; can be empty to delete that span; must not be identical to old_string'},
                                             'newText': {'type': 'string', 'description': 'Synonym for new_string, either one works'},
                                             'replace_all': {'type': 'boolean',
                                                             'description': 'Defaults to false. When true, replaces every occurrence of '
                                                                            'old_string in the file; used for scenarios like bulk renaming. '
                                                                            'When false, an error is raised if old_string isn\'t unique.'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'local_task_list',
               'description': 'List all background tasks currently held in the local executor\'s memory (processes started by '
                              'local_exec_command with background=true), including running / exited / killed status. **The registry is kept '
                              'in the executor\'s local memory, and is cleared if the executor process restarts** (this doesn\'t mean the '
                              'process is gone, only that Flops can no longer see it). Optional running_only shows only tasks still running.',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID. If omitted, the device bound to the current session is used.'},
                                             'running_only': {'type': 'boolean',
                                                              'description': 'Optional. true means return only tasks with status=running; defaults to false, returning all.'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'local_task_stop',
               'description': 'Stop a background task on the local executor. Sends SIGTERM to the whole process group first, then SIGKILL if '
                              'it\'s still alive after grace_seconds (on Windows, uses taskkill /F /T). **Prefer this tool** over writing your '
                              'own `kill <pid>` -- the latter can\'t kill grandchild processes.',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID. If omitted, the device bound to the current session is used.'},
                                             'task_id': {'type': 'string',
                                                         'description': 'The background task ID to stop (the task_id returned by local_exec_command with background; can also be obtained from local_task_list).'},
                                             'grace_seconds': {'type': 'number',
                                                               'description': 'Optional. Seconds to wait after SIGTERM, defaults to 3. 0 means kill immediately.'}},
                              'required': ['task_id']}}},
 {'type': 'function',
  'function': {'name': 'local_task_wait',
               'description': '**Explicitly block** and wait for the specified background task to finish (up to timeout_seconds seconds, '
                              'capped at 180). A task that has already exited returns immediately. On completion, returns final_status / '
                              'exit_code / log_tail, saving a local_read_file call. **Only use this when you genuinely need to wait for the '
                              'result before deciding the next step**; otherwise stick with the system-notification mode. Each wait occupies '
                              'a worker-thread slot on the executor -- **keep the number of concurrent waits to 1-2** to avoid blocking other '
                              'local tools.',
               'parameters': {'type': 'object',
                              'properties': {'device_id': {'type': 'string',
                                                           'description': 'Optional. Target executor ID. If omitted, the device bound to the current session is used.'},
                                             'task_id': {'type': 'string',
                                                         'description': 'The background task ID to wait for (the task_id returned by local_exec_command with background; can also be obtained from local_task_list).'},
                                             'timeout_seconds': {'type': 'number',
                                                                 'description': 'Optional. Maximum wait time in seconds, defaults to 30; the executor\'s hard cap is 180 (values above that are clamped).'}},
                              'required': ['task_id']}}}]

BASIC_TOOL_REQUIRES: Dict[str, List[str]] = {'local_exec_command': ['executor.command.exec'],
 'local_list_files': ['executor.files.list'],
 'local_search_files': ['executor.files.search'],
 'local_read_file': ['executor.files.read'],
 'local_write_file': ['executor.files.write'],
 'local_edit_file': ['executor.files.write'],
 'local_task_list': ['executor.task.list'],
 'local_task_stop': ['executor.task.stop'],
 'local_task_wait': ['executor.task.wait']}


def register_on_executor_package(
    registry: Optional[ToolRegistry] = None,
    *,
    group: Optional[Dict[str, Any]] = None,
    basic: Optional[Dict[str, Any]] = None,
    package_requires: Iterable[str] = ("executor:online",),
) -> List[str]:
    """Register the group package, the basic package, and the nine tools (all with ``route=ROUTE_EXECUTOR`` + executor capability tags).

    ``group`` / ``basic``: package copy ``{"name", "description", "system_prompt"}``. When None:
    if the product layer already registered that path, its copy is kept; otherwise the
    framework's default copy is used. ``package_requires``: the package-level capability
    requirement shared by both packages (the product layer's "an executor is online" tag);
    pass an empty sequence to leave it unset. Returns the names of the registered tools.
    """
    reg = registry if registry is not None else DEFAULT_REGISTRY
    _ensure_package(reg, PACKAGE_PATH, group, DEFAULT_GROUP_NAME, DEFAULT_GROUP_DESCRIPTION, DEFAULT_GROUP_PROMPT)
    _ensure_package(reg, BASIC_PATH, basic, DEFAULT_BASIC_NAME, DEFAULT_BASIC_DESCRIPTION, DEFAULT_BASIC_PROMPT)
    requires = [str(t) for t in package_requires]
    if requires:
        reg.package_requires[PACKAGE_PATH] = list(requires)
        reg.package_requires[BASIC_PATH] = list(requires)
    names: List[str] = []
    for tool_def in BASIC_TOOLS:
        fn = cast(Dict[str, Any], tool_def["function"])
        name = str(fn["name"])
        reg.register_tool(
            BASIC_PATH, name, tool_def, None,
            requires=list(BASIC_TOOL_REQUIRES.get(name, [])), route=ROUTE_EXECUTOR,
        )
        names.append(name)
    return names


def _ensure_package(
    reg: ToolRegistry, path: str, copy: Optional[Dict[str, Any]],
    default_name: str, default_description: str, default_prompt: str,
) -> None:
    if copy is not None:
        reg.register_package(
            path, name=str(copy.get("name") or default_name),
            description=str(copy.get("description") or default_description),
            system_prompt=copy.get("system_prompt"),
        )
        return
    if path in reg.packages:
        return   # The product layer already registered its own copy first: keep it
    reg.register_package(path, name=default_name, description=default_description, system_prompt=default_prompt)


__all__ = [
    "PACKAGE_PATH", "BASIC_PATH", "BASIC_TOOLS", "BASIC_TOOL_REQUIRES", "register_on_executor_package",
]
