"""
R.O.S.E (Project Mirror) - Tool Registry
Central, strictly typed, permission-checked tool layer.
Rules A2, A3, A5: Truthful results, strict schemas, audit logging, and permission enforcement.
"""

from __future__ import annotations
import asyncio
import concurrent.futures
import inspect
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from core.audit import audit_log
from core.logging_setup import get_logger
from core.permissions import Tier, security_manager
from core.result import Result

logger = get_logger("registry")


def validate_schema(schema: Dict[str, Any], args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Lightweight, robust JSON schema validator for tool call arguments.
    Checks: required fields, types, and enums.
    """
    if not isinstance(args, dict):
        return False, f"Arguments must be a JSON object, got {type(args).__name__}"

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # 1. Required fields check
    for req in required:
        if req not in args or args[req] is None:
            return False, f"Missing required parameter: '{req}'"

    # 2. Type & enum checks for provided fields
    type_map = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list, tuple),
        "object": (dict,),
    }

    for key, val in args.items():
        if key not in properties:
            # Allow extra properties unless additionalProperties is explicitly False
            if schema.get("additionalProperties") is False:
                return False, f"Unknown unexpected parameter: '{key}'"
            continue

        spec = properties[key]
        expected_type_str = spec.get("type")

        # Type validation
        if expected_type_str and expected_type_str in type_map:
            # Note: in Python, bool is a subclass of int, so handle boolean vs int carefully
            if expected_type_str in ("integer", "number") and isinstance(val, bool):
                return False, f"Parameter '{key}' expected {expected_type_str}, got boolean"
            expected_types = type_map[expected_type_str]
            if not isinstance(val, expected_types):
                return False, f"Parameter '{key}' expected {expected_type_str}, got {type(val).__name__}"

        # Enum validation
        if "enum" in spec:
            allowed = spec["enum"]
            # Case-insensitive comparison if strings
            if isinstance(val, str):
                allowed_lower = [str(x).lower() for x in allowed]
                if val.lower() not in allowed_lower:
                    return False, f"Parameter '{key}' must be one of {allowed}, got '{val}'"
            elif val not in allowed:
                return False, f"Parameter '{key}' must be one of {allowed}, got '{val}'"

    return True, "Valid"


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]
    permission_tier: int = Tier.TIER_0_READ_ONLY
    handler: Optional[Callable[..., Any]] = None
    standalone_test: Optional[Callable[[], Union[Result, bool, dict]]] = None
    timeout: float = 15.0


class ToolRegistry:
    """Central registry of all tools available to R.O.S.E."""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="ROSE-ToolWorker"
        )

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        permission_tier: int = Tier.TIER_0_READ_ONLY,
        timeout: float = 15.0,
        standalone_test: Optional[Callable[[], Union[Result, bool, dict]]] = None,
    ):
        """Decorator to register a tool handler function."""
        def decorator(fn: Callable[..., Any]):
            self._tools[name] = ToolDefinition(
                name=name,
                description=description.strip(),
                parameters=parameters,
                permission_tier=permission_tier,
                handler=fn,
                standalone_test=standalone_test,
                timeout=timeout,
            )
            return fn
        return decorator

    def register_tool(self, tool_def: ToolDefinition) -> None:
        self._tools[tool_def.name] = tool_def

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def to_gemini_declarations(self) -> List[Dict[str, Any]]:
        """Converts registered tools into Gemini Live API function declarations."""
        decls = []
        for tool in self._tools.values():
            decls.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            })
        return decls

    async def async_execute(
        self,
        name: str,
        args: Dict[str, Any],
        confirmed: bool = False,
        source: str = "voice",
        voice_verified: bool = False,
    ) -> Result:
        """
        Executes a registered tool asynchronously with full validation,
        permission checks, timeout enforcement, exception handling, and audit logging.
        """
        t_start = time.time()
        tool = self._tools.get(name)

        if not tool:
            err = f"Unknown tool: '{name}'"
            logger.warning(err)
            res = Result.failure(err)
            audit_log(name, args, Tier.TIER_0_READ_ONLY, res, source, confirmed)
            return res

        # 1. Validate argument schema (Rule A3)
        valid, err_msg = validate_schema(tool.parameters, args)
        if not valid:
            logger.warning(f"Schema violation for '{name}': {err_msg}")
            res = Result.failure(f"Argument schema validation failed: {err_msg}")
            audit_log(name, args, tool.permission_tier, res, source, confirmed)
            return res

        # 2. Check permissions and kill switch (Rule A5)
        allowed, perm_msg = security_manager.check_tool_permission(
            tool_name=name,
            tier=tool.permission_tier,
            confirmed=confirmed,
            source=source,
            voice_verified=voice_verified,
        )
        if not allowed:
            logger.warning(f"Permission denied for '{name}': {perm_msg}")
            res = Result.failure(perm_msg)
            audit_log(name, args, tool.permission_tier, res, source, confirmed)
            return res

        # 3. Execute with timeout
        if not tool.handler:
            res = Result.failure(f"No handler defined for tool '{name}'")
            audit_log(name, args, tool.permission_tier, res, source, confirmed)
            return res

        try:
            loop = asyncio.get_running_loop()
            if inspect.iscoroutinefunction(tool.handler):
                coro = tool.handler(**args)
                raw_res = await asyncio.wait_for(coro, timeout=tool.timeout)
            else:
                func = lambda: tool.handler(**args)
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._thread_pool, func),
                    timeout=tool.timeout,
                )

            # 4. Standardize output into Result
            if isinstance(raw_res, Result):
                final_res = raw_res
            elif isinstance(raw_res, bool):
                final_res = Result.success(verified=raw_res) if raw_res else Result.failure("Operation returned false")
            elif isinstance(raw_res, dict):
                ok = raw_res.get("ok", True)
                ver = raw_res.get("verified", ok)
                err = raw_res.get("error")
                final_res = Result(ok=ok, data=raw_res, error=err, verified=ver)
            elif isinstance(raw_res, str):
                final_res = Result.success(data={"message": raw_res}, verified=True)
            elif raw_res is None:
                final_res = Result.success(data={"message": f"{name} executed"}, verified=False)
            else:
                final_res = Result.success(data={"output": raw_res}, verified=True)

        except asyncio.TimeoutError:
            msg = f"Tool '{name}' timed out after {tool.timeout:.1f}s"
            logger.error(msg)
            final_res = Result.failure(msg)
        except Exception as e:
            logger.error(f"Unhandled exception in tool '{name}': {e}\n{traceback.format_exc()}")
            # Human-friendly short error message without raw traceback
            human_err = f"{type(e).__name__}: {str(e) or 'Internal error'}"
            final_res = Result.failure(human_err)

        dur_ms = (time.time() - t_start) * 1000.0
        logger.info(f"Tool '{name}' completed in {dur_ms:.1f}ms: ok={final_res.ok}, verified={final_res.verified}")

        # 5. Record to audit log
        audit_log(name, args, tool.permission_tier, final_res, source, confirmed)
        return final_res

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        confirmed: bool = False,
        source: str = "voice",
        voice_verified: bool = False,
    ) -> Result:
        """Synchronous wrapper for async_execute."""
        tool_def = self.get_tool(name)
        sync_timeout = max(16.0, float(tool_def.timeout) + 2.0) if tool_def else 16.0
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                future = concurrent.futures.Future()
                asyncio.run_coroutine_threadsafe(
                    self.async_execute(name, args, confirmed, source, voice_verified),
                    loop,
                ).add_done_callback(lambda f: future.set_result(f.result()))
                return future.result(timeout=sync_timeout)
            else:
                return loop.run_until_complete(
                    self.async_execute(name, args, confirmed, source, voice_verified)
                )
        except RuntimeError:
            return asyncio.run(
                self.async_execute(name, args, confirmed, source, voice_verified)
            )


# Global singleton registry
registry = ToolRegistry()

