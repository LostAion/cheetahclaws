"""Core agent loop: neutral message format, multi-provider streaming."""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Generator

from tool_registry import get_tool_schemas
from tools import execute_tool
import tools as _tools_init  # ensure built-in tools are registered on import
from providers import stream, AssistantTurn, TextChunk, ThinkingChunk, detect_provider
from compaction import maybe_compact, estimate_tokens, get_context_limit, compact_messages, sanitize_history
import logging_utils as _log
import quota as _quota
from circuit_breaker import CircuitOpenError as _CircuitOpenError
import runtime
from audit.recorder import log_turn

# ── Re-export event types (used by cheetahclaws.py) ────────────────────────
__all__ = [
    "AgentState", "run",
    "TextChunk", "ThinkingChunk",
    "ToolStart", "ToolEnd", "TurnDone", "PermissionRequest",
]


@dataclass
class AgentState:
    """Mutable session state. messages use the neutral provider-independent format."""
    messages: list = field(default_factory=list)
    total_input_tokens:  int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens:  int = 0
    total_cache_write_tokens: int = 0
    turn_count: int = 0


@dataclass
class ToolStart:
    name:   str
    inputs: dict

@dataclass
class ToolEnd:
    name:      str
    result:    str
    permitted: bool = True

@dataclass
class TurnDone:
    input_tokens:  int
    output_tokens: int

@dataclass
class PermissionRequest:
    description: str
    granted: bool = False


# ── Agent loop ─────────────────────────────────────────────────────────────

def run(
    user_message: str,
    state: AgentState,
    config: dict,
    system_prompt: str,
    depth: int = 0,
    cancel_check=None,
) -> Generator:
    """
    Multi-turn agent loop (generator).
    Yields: TextChunk | ThinkingChunk | ToolStart | ToolEnd |
            PermissionRequest | TurnDone

    Args:
        depth: sub-agent nesting depth, 0 for top-level
        cancel_check: callable returning True to abort the loop early
    """
    # Append user turn in neutral format
    user_msg = {"role": "user", "content": user_message}
    # Attach pending image from /image command if present
    sctx = runtime.get_ctx(config)
    pending_img = sctx.pending_image
    sctx.pending_image = None
    if pending_img:
        user_msg["images"] = [pending_img]
    state.messages.append(user_msg)
    log_turn(config.get("_session_id", "default"), "user", user_message)

    # Inject runtime metadata into config so tools (e.g. Agent) can access it
    config = {**config, "_depth": depth, "_system_prompt": system_prompt}
    session_id = config.get("_session_id", "default")

    # Wire up structured logging from config (idempotent, cheap)
    _log.configure_from_config(config)

    while True:
        if cancel_check and cancel_check():
            return
        state.turn_count += 1
        assistant_turn: AssistantTurn | None = None

        # Compact context if approaching window limit
        try:
            maybe_compact(state, config)
        except Exception as _compact_err:
            _log.warn("compact_failed", error=str(_compact_err))

        # Enforce tool_calls ↔ tool-response pairing before every API call.
        # Defends against compaction artifacts, crashed tool execs, or any
        # other source of orphan 'tool' messages that OpenAI-compatible
        # providers (DeepSeek et al.) reject with a 400.
        _before_len = len(state.messages)
        state.messages = sanitize_history(state.messages)
        if len(state.messages) != _before_len:
            _log.warn("history_sanitized",
                      session_id=session_id,
                      removed=_before_len - len(state.messages))

        # ── Quota check — before spending tokens ──────────────────────────
        try:
            _quota.check_quota(session_id, config)
        except _quota.QuotaExceeded as qe:
            _log.warn("quota_exceeded", session_id=session_id, reason=qe.reason)
            yield TextChunk(f"\n[Quota exceeded — {qe.reason}]\n")
            break

        # Stream from provider — retry on ANY error (never crash the session)
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                for event in stream(
                    model=config["model"],
                    system=system_prompt,
                    messages=state.messages,
                    tool_schemas=get_tool_schemas(),
                    config=config,
                ):
                    if isinstance(event, (TextChunk, ThinkingChunk)):
                        yield event
                    elif isinstance(event, AssistantTurn):
                        assistant_turn = event
                        # Record usage for quota tracking
                        _quota.record_usage(
                            session_id, config["model"],
                            event.in_tokens, event.out_tokens,
                        )
                break  # success — exit retry loop

            except _CircuitOpenError as e:
                _log.warn("circuit_open_skip", session_id=session_id,
                          error=str(e)[:200])
                yield TextChunk(f"\n[{e}]\n")
                return  # circuit manages its own cooldown — don't retry

            except Exception as e:
                from error_classifier import classify as _classify_err
                cerr = _classify_err(e)

                if attempt >= max_retries or not cerr.retryable:
                    _log.error("api_failed", session_id=session_id,
                               error_type=type(e).__name__,
                               category=cerr.category.value,
                               error=_truncate_err(str(e)))
                    hint = f" Hint: {cerr.hint}" if cerr.hint else ""
                    yield TextChunk(f"\n[Failed — {type(e).__name__}: {_truncate_err(str(e))}.{hint}]\n")
                    break

                if cerr.should_compress:
                    _force_compact(state, config)
                    yield TextChunk(f"\n[Context too long — compacted and retrying (attempt {attempt+1}/{max_retries})]\n")
                    continue

                backoff = int(2 ** (attempt + 1) * cerr.backoff_multiplier)
                backoff = min(backoff, 30)
                _log.warn("api_retry", session_id=session_id,
                          attempt=attempt + 1, max_retries=max_retries,
                          category=cerr.category.value,
                          error_type=type(e).__name__,
                          error=_truncate_err(str(e)),
                          backoff_s=backoff)
                yield TextChunk(f"\n[Retry {attempt+1}/{max_retries} after {backoff}s — {cerr.category.value}: {_truncate_err(str(e))}]\n")
                time.sleep(backoff)

        if assistant_turn is None:
            break

        # Record assistant turn in neutral format
        _assistant_msg = {
            "role":       "assistant",
            "content":    assistant_turn.text,
            "tool_calls": assistant_turn.tool_calls,
        }
        # DeepSeek v4 requires reasoning_content to be echoed back on
        # subsequent requests when the turn contains tool_calls.  Storing it
        # on the neutral history lets messages_to_openai pass it through.
        _rc = getattr(assistant_turn, "reasoning_content", "")
        if _rc and assistant_turn.tool_calls:
            _assistant_msg["reasoning_content"] = _rc
        state.messages.append(_assistant_msg)
        log_turn(session_id, "assistant", assistant_turn.text)

        state.total_input_tokens  += assistant_turn.in_tokens
        state.total_output_tokens += assistant_turn.out_tokens
        state.total_cache_read_tokens  += getattr(assistant_turn, 'cache_read_tokens', 0)
        state.total_cache_write_tokens += getattr(assistant_turn, 'cache_write_tokens', 0)
        yield TurnDone(assistant_turn.in_tokens, assistant_turn.out_tokens)

        if not assistant_turn.tool_calls:
            break   # No tools → conversation turn complete

        # ── Execute tools (parallel when safe) ────────────────────────────
        tool_calls = assistant_turn.tool_calls

        # Check permissions first (must be sequential — may prompt user)
        permissions: dict[str, bool] = {}
        for tc in tool_calls:
            permitted = _check_permission(tc, config)
            if not permitted:
                if config.get("permission_mode") == "plan":
                    permitted = False
                else:
                    if config.get("_headless", False):
                        permitted = False
                    else:
                        req = PermissionRequest(description=_permission_desc(tc))
                        yield req
                        permitted = req.granted
            permissions[tc["id"]] = permitted

        # Determine which tools can run in parallel
        from tool_registry import get_tool as _get_tool
        parallel_batch = []
        sequential_batch = []
        for tc in tool_calls:
            if not permissions[tc["id"]]:
                sequential_batch.append(tc)
                continue
            tdef = _get_tool(tc["name"])
            if tdef and tdef.concurrent_safe and len(tool_calls) > 1:
                parallel_batch.append(tc)
            else:
                sequential_batch.append(tc)

        def _exec_one(tc):
            """Execute a single tool call, return (tc, result, permitted)."""
            tid = tc["id"]
            permitted = permissions[tid]
            if not permitted:
                if config.get("permission_mode") == "plan":
                    plan_file = runtime.get_ctx(config).plan_file or ""
                    result = (
                        f"[Plan mode] Write operations are blocked except to the plan file: {plan_file}\n"
                        "Finish your analysis and write the plan to the plan file. "
                        "The user will run /plan done to exit plan mode and begin implementation."
                    )
                else:
                    result = "Denied: user rejected this operation"
            else:
                result = execute_tool(
                    tc["name"], tc["input"],
                    permission_mode="accept-all",
                    config=config,
                )
            return tc, result, permitted

        results_ordered = []

        # Run parallel batch concurrently
        if parallel_batch:
            from concurrent.futures import ThreadPoolExecutor
            for tc in parallel_batch:
                yield ToolStart(tc["name"], tc["input"])
            with ThreadPoolExecutor(max_workers=min(len(parallel_batch), 8)) as pool:
                futures = {pool.submit(_exec_one, tc): tc for tc in parallel_batch}
                for future in futures:
                    tc, result, permitted = future.result()
                    _log.debug("tool_end", session_id=session_id,
                               tool=tc["name"], permitted=permitted,
                               result_len=len(result))
                    results_ordered.append((tc, result, permitted))

        # Run sequential batch one by one
        for tc in sequential_batch:
            yield ToolStart(tc["name"], tc["input"])
            _log.debug("tool_start", session_id=session_id,
                       tool=tc["name"], input_keys=list(tc["input"].keys()))
            tc, result, permitted = _exec_one(tc)
            _log.debug("tool_end", session_id=session_id,
                       tool=tc["name"], permitted=permitted,
                       result_len=len(result))
            results_ordered.append((tc, result, permitted))

        # Yield results and append to state in original order
        for tc, result, permitted in results_ordered:
            yield ToolEnd(tc["name"], result, permitted)
            state.messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "name":         tc["name"],
                "content":      result,
            })


# ── Helpers ───────────────────────────────────────────────────────────────

def _check_permission(tc: dict, config: dict) -> bool:
    """Return True if operation is auto-approved (no need to ask user)."""
    perm_mode = config.get("permission_mode", "auto")
    name = tc["name"]

    # Plan mode tools are always auto-approved
    if name in ("EnterPlanMode", "ExitPlanMode"):
        return True

    if perm_mode == "accept-all":
        return True
    if perm_mode == "manual":
        return False   # always ask

    if perm_mode == "plan":
        # Allow writes ONLY to the plan file
        if name in ("Write", "Edit"):
            plan_file = runtime.get_ctx(config).plan_file or ""
            target = tc["input"].get("file_path", "")
            if plan_file and target and \
               os.path.normpath(target) == os.path.normpath(plan_file):
                return True
            return False
        if name == "NotebookEdit":
            return False
        if name == "Bash":
            from tools import _is_safe_bash
            return _is_safe_bash(tc["input"].get("command", ""))
        return True  # reads are fine

    # "auto" mode: only ask for writes and non-safe bash
    if name in ("Read", "Glob", "Grep", "WebFetch", "WebSearch"):
        return True
    if name == "Bash":
        from tools import _is_safe_bash
        return _is_safe_bash(tc["input"].get("command", ""))
    return False   # Write, Edit → ask


def _permission_desc(tc: dict) -> str:
    name = tc["name"]
    inp  = tc["input"]
    if name == "Bash":   return f"Run: {inp.get('command', '')}"
    if name == "Write":  return f"Write to: {inp.get('file_path', '')}"
    if name == "Edit":   return f"Edit: {inp.get('file_path', '')}"
    return f"{name}({list(inp.values())[:1]})"


def _force_compact(state: AgentState, config: dict) -> bool:
    """Force compaction regardless of threshold. Used when API rejects for context too long."""
    limit = get_context_limit(config.get("model", ""))
    before = estimate_tokens(state.messages)
    if before <= 0:
        return False
    from compaction import snip_old_tool_results
    snip_old_tool_results(state.messages, max_chars=1000, preserve_last_n_turns=3)
    if estimate_tokens(state.messages) < limit * 0.9:
        return True
    state.messages = compact_messages(state.messages, config)
    from compaction import _restore_plan_context
    state.messages.extend(_restore_plan_context(config))
    after = estimate_tokens(state.messages)
    return after < before


def _truncate_err(s: str, max_len: int = 120) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."

"""
agent_runner.py — Autonomous agent loop driven by task templates.

Design
------
* Each AgentRunner owns an isolated AgentState (separate from the main REPL).
* Templates are Markdown files (built-ins in agent_templates/ or user-supplied
  path) describing what the agent should do, inspired by Karpathy's autoresearch
  program.md pattern.
* The loop calls agent.run() for each iteration, draining the generator.
  PermissionRequests are auto-granted (autonomous mode) with a notification.
* After each iteration a ≤500-char summary is sent via send_fn (bridge / terminal).
* Iteration history is persisted to ~/.cheetahclaws/agents/<name>/log.jsonl.
* call stop() or send_fn receives "!agent-stop" to terminate the loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import logging_utils as _log

# ── Template resolution ────────────────────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).parent / "agent_templates"
_USER_TEMPLATES_DIR = Path.home() / ".cheetahclaws" / "agent_templates"


def list_templates() -> list[dict]:
    """Return all known templates (built-in + user-defined)."""
    result = []
    for d, source in [(_TEMPLATES_DIR, "built-in"), (_USER_TEMPLATES_DIR, "user")]:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            result.append({"name": f.stem, "source": source, "path": str(f)})
    return result


def load_template(name_or_path: str) -> tuple[str, str]:
    """Load a template by name or file path.

    Returns (template_content, resolved_path).
    Raises FileNotFoundError if not found.
    """
    p = Path(name_or_path)
    if p.exists():
        return p.read_text(encoding="utf-8"), str(p)

    # Search built-in then user
    for d in [_USER_TEMPLATES_DIR, _TEMPLATES_DIR]:
        candidate = d / f"{name_or_path}.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8"), str(candidate)

    available = [t["name"] for t in list_templates()]
    raise FileNotFoundError(
        f"Template '{name_or_path}' not found. "
        f"Available: {', '.join(available) or '(none)'}"
    )


# ── Registry ───────────────────────────────────────────────────────────────

_runners: dict[str, "AgentRunner"] = {}
_runners_lock = threading.Lock()


def get_runner(name: str) -> "AgentRunner | None":
    with _runners_lock:
        r = _runners.get(name)
        if r and not r.is_alive:
            _runners.pop(name, None)
            return None
        return r


def list_runners() -> list["AgentRunner"]:
    with _runners_lock:
        return list(_runners.values())


def start_runner(
    name: str,
    template_name: str,
    args: str,
    config: dict,
    send_fn: Optional[Callable[[str], None]] = None,
    interval: float = 2.0,
    auto_approve: bool = True,
) -> "AgentRunner":
    """Create and start an AgentRunner; kill any previous runner with same name."""
    template_content, template_path = load_template(template_name)
    runner = AgentRunner(
        name=name,
        template_content=template_content,
        template_path=template_path,
        args=args,
        config=config,
        send_fn=send_fn,
        interval=interval,
        auto_approve=auto_approve,
    )
    with _runners_lock:
        old = _runners.get(name)
        if old:
            old.stop()
        _runners[name] = runner
    runner.start()
    return runner


def stop_runner(name: str) -> bool:
    with _runners_lock:
        r = _runners.pop(name, None)
    if r:
        r.stop()
        return True
    return False


def stop_all() -> int:
    with _runners_lock:
        runners = list(_runners.values())
        _runners.clear()
    for r in runners:
        r.stop()
    return len(runners)


# ── AgentRunner ────────────────────────────────────────────────────────────

_LOG_DIR = Path.home() / ".cheetahclaws" / "agents"


@dataclass
class _IterationRecord:
    iteration: int
    timestamp: str
    summary: str
    status: str  # "ok" | "error" | "permission"
    duration_s: float


class AgentRunner:
    """Runs an autonomous agent loop driven by a task template."""

    def __init__(
        self,
        name: str,
        template_content: str,
        template_path: str,
        args: str,
        config: dict,
        send_fn: Optional[Callable[[str], None]],
        interval: float = 2.0,
        auto_approve: bool = True,
    ) -> None:
        self.name = name
        self.template = template_content
        self.template_path = template_path
        self.args = args
        self._config = config.copy()
        self.send_fn = send_fn
        self.interval = interval
        self.auto_approve = auto_approve

        self.iteration = 0
        self.status = "idle"
        self._stop_event = threading.Event()
        self._history: list[_IterationRecord] = []
        self._thread: threading.Thread | None = None
        self._log_dir = _LOG_DIR / name
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── Public interface ───────────────────────────────────────────────────

    def start(self) -> None:
        self.status = "starting"
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True,
            name=f"agent-{self.name}",
        )
        self._thread.start()
        _log.info("agent_runner_start", name=self.name,
                  template=self.template_path, args=self.args[:100])

    def stop(self) -> None:
        self._stop_event.set()
        self.status = "stopping"
        _log.info("agent_runner_stop", name=self.name, iteration=self.iteration)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def recent_log(self, n: int = 5) -> list[_IterationRecord]:
        return self._history[-n:]

    def summary_text(self) -> str:
        lines = [f"Agent: {self.name}  status={self.status}  iter={self.iteration}"]
        for rec in self.recent_log(3):
            lines.append(f"  [{rec.iteration}] {rec.status} ({rec.duration_s:.1f}s): {rec.summary[:120]}")
        return "\n".join(lines)

    # ── Internal loop ──────────────────────────────────────────────────────

    def _notify(self, text: str) -> None:
        """Send a message to the phone/terminal."""
        if self.send_fn:
            try:
                self.send_fn(text)
            except Exception:
                pass
        else:
            print(text)

    def _run_loop(self) -> None:
        from core.engine import AgentState, PermissionRequest, TurnDone
        from core.engine import TextChunk, ToolStart, ToolEnd

        state = AgentState()
        config = self._config.copy()
        config["_auto_agent"] = True
        config["_auto_approve"] = self.auto_approve

        system_prompt = (
            "You are an autonomous agent executing the following task program. "
            "Run it faithfully and autonomously. After completing each iteration, "
            "write a brief 1-2 sentence summary of what you did and what you'll do next.\n\n"
            f"=== TASK PROGRAM ===\n{self.template}\n=== END PROGRAM ==="
        )

        self.status = "running"
        self._notify(
            f"🚀 Agent **{self.name}** started.\n"
            f"Template: `{Path(self.template_path).name}`\n"
            f"Args: {self.args or '(none)'}\n"
            f"Auto-approve: {self.auto_approve}\n"
            "Send `!agent stop {name}` to stop."
        )

        iteration = 0
        while not self._stop_event.is_set():
            iteration += 1
            self.iteration = iteration
            self.status = f"running (iter {iteration})"
            t_start = time.monotonic()

            prompt = (
                f"Begin the program. Args: {self.args}" if iteration == 1 and self.args
                else "Begin the program." if iteration == 1
                else "Continue to the next iteration of the program."
            )

            text_chunks: list[str] = []
            rec_status = "ok"

            try:
                for event in sys.modules[__name__].run(
                    prompt, state, config, system_prompt
                ):
                    if self._stop_event.is_set():
                        break

                    if isinstance(event, TextChunk):
                        text_chunks.append(event.text)

                    elif isinstance(event, PermissionRequest):
                        if self.auto_approve:
                            event.granted = True
                            self._notify(
                                f"🔐 [{self.name}] Auto-approved: {event.description[:120]}"
                            )
                            rec_status = "permission"
                        else:
                            self._notify(
                                f"🔐 [{self.name}] Permission needed (agent paused):\n"
                                f"{event.description}\n\n"
                                "The agent cannot continue without approval. "
                                "Restart with `--auto-approve` to enable autonomous mode."
                            )
                            event.granted = False
                            self._stop_event.set()
                            break

                    elif isinstance(event, ToolStart):
                        cmd_preview = str(
                            (event.inputs or {}).get("command",
                             (event.inputs or {}).get("file_path", ""))
                        ).strip()[:60]
                        _log.debug("agent_tool_start", name=self.name,
                                   tool=event.name, cmd=cmd_preview)

            except Exception as exc:
                rec_status = "error"
                err_msg = str(exc)[:300]
                text_chunks.append(f"\n[ERROR: {err_msg}]")
                self._notify(f"⚠ [{self.name}] iter {iteration} error:\n{err_msg}")
                _log.warn("agent_runner_error", name=self.name, iteration=iteration,
                          error=err_msg)
                # Brief pause before retrying
                self._stop_event.wait(10.0)

            duration = time.monotonic() - t_start
            summary = "".join(text_chunks).strip()[-400:] or "(no output)"

            rec = _IterationRecord(
                iteration=iteration,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                summary=summary[:400],
                status=rec_status,
                duration_s=round(duration, 1),
            )
            self._history.append(rec)
            self._persist_record(rec)

            # Report iteration result
            if rec_status != "error":
                self._notify(
                    f"✅ [{self.name}] iter {iteration} ({duration:.0f}s):\n"
                    f"{summary[:400]}"
                )

            _log.info("agent_runner_iter", name=self.name, iteration=iteration,
                      status=rec_status, duration_s=rec.duration_s)

            # Wait before next iteration (stop event wakes it early)
            self._stop_event.wait(self.interval)

        self.status = "stopped"
        self._notify(f"⏹ Agent **{self.name}** stopped after {iteration} iterations.")
        _log.info("agent_runner_stopped", name=self.name, iterations=iteration)

    def _persist_record(self, rec: _IterationRecord) -> None:
        log_file = self._log_dir / "log.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "iteration": rec.iteration,
                    "timestamp": rec.timestamp,
                    "status": rec.status,
                    "duration_s": rec.duration_s,
                    "summary": rec.summary,
                }) + "\n")
        except Exception:
            pass
