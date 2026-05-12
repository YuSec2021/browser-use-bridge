from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from browser_use.tools.actions import (
    ClickParams,
    DoneParams,
    ExtractContentParams,
    GoBackParams,
    InputTextParams,
    NavigateParams,
    ScrollParams,
    click,
    done,
    extract_content,
    go_back,
    input_text,
    navigate,
    scroll,
)
from browser_use.tools.registry import ActionRegistry, params_model_from_callable


class Tools:
    def __init__(self) -> None:
        self.registry = ActionRegistry()
        self._register_builtin_actions()

    def list_actions(self) -> list[dict[str, Any]]:
        return [action.to_metadata() for action in self.registry.list()]

    def create_action_model(self) -> type[BaseModel]:
        return self.registry.create_action_model()

    def action(self, description: str | None = None, name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            action_name = name or function.__name__
            action_description = description or inspect.getdoc(function) or action_name.replace("_", " ")
            self.registry.register(
                name=action_name,
                description=action_description,
                handler=function,
                params_model=params_model_from_callable(action_name, function),
            )
            return function

        return decorator

    async def execute_action(
        self,
        action: dict[str, Any] | BaseModel,
        browser_session: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        action_payload = self._normalize_action(action)
        if len(action_payload) != 1:
            raise ValueError("Exactly one action must be provided")
        action_name, raw_params = next(iter(action_payload.items()))
        registration = self.registry.get(action_name)
        params = registration.params_model.model_validate(raw_params or {})
        call_kwargs = params.model_dump()
        if registration.requires_browser:
            if browser_session is None:
                raise RuntimeError(f"{action_name} action requires a browser session")
            call_kwargs["browser_session"] = browser_session
        call_kwargs.update(kwargs)

        result = registration.handler(**call_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _register_builtin_actions(self) -> None:
        self.registry.register(
            "navigate",
            "Navigate the active browser tab to a URL.",
            navigate,
            NavigateParams,
            requires_browser=True,
        )
        self.registry.register(
            "click",
            "Click an indexed interactive element on the current page.",
            click,
            ClickParams,
            requires_browser=True,
        )
        self.registry.register(
            "input_text",
            "Type text into an indexed input, textarea, or editable element.",
            input_text,
            InputTextParams,
            requires_browser=True,
        )
        self.registry.register(
            "scroll",
            "Scroll the current page vertically by the requested pixel amount.",
            scroll,
            ScrollParams,
            requires_browser=True,
        )
        self.registry.register(
            "extract_content",
            "Extract readable page content and indexed interactive elements.",
            extract_content,
            ExtractContentParams,
            requires_browser=True,
        )
        self.registry.register(
            "go_back",
            "Go back to the previous page in the active tab history.",
            go_back,
            GoBackParams,
            requires_browser=True,
        )
        self.registry.register(
            "done",
            "Mark the task as complete and return final status text.",
            done,
            DoneParams,
        )

    def _normalize_action(self, action: dict[str, Any] | BaseModel) -> dict[str, Any]:
        if isinstance(action, BaseModel):
            return action.model_dump(exclude_none=True)
        if not isinstance(action, dict):
            raise TypeError("Action must be a dictionary or Pydantic model")
        return action


__all__ = ["Tools"]
