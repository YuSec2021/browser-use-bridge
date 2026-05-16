from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, create_model


ActionHandler = Callable[..., Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class ActionRegistration:
    name: str
    description: str
    params_model: type[BaseModel]
    handler: ActionHandler
    requires_browser: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "schema": self.params_model.model_json_schema(),
        }


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionRegistration] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: ActionHandler,
        params_model: type[BaseModel] | None = None,
        requires_browser: bool = False,
    ) -> ActionRegistration:
        model = params_model or params_model_from_callable(name, handler)
        registration = ActionRegistration(
            name=name,
            description=description,
            params_model=model,
            handler=handler,
            requires_browser=requires_browser,
        )
        self._actions[name] = registration
        return registration

    def get(self, name: str) -> ActionRegistration:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown action: {name}") from exc

    def list(self) -> list[ActionRegistration]:
        return list(self._actions.values())

    def create_action_model(self) -> type[BaseModel]:
        fields: dict[str, tuple[Any, Any]] = {
            action.name: (
                Optional[action.params_model],
                Field(default=None, description=action.description),
            )
            for action in self.list()
        }

        def validate_exactly_one(self: BaseModel) -> BaseModel:
            selected = [
                name
                for name in fields
                if getattr(self, name, None) is not None
            ]
            if len(selected) != 1:
                raise ValueError("Exactly one action must be provided")
            return self

        validators = {
            "_validate_exactly_one": _after_model_validator(validate_exactly_one)
        }
        return create_model(
            "ActionModel",
            __validators__=validators,
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )


def params_model_from_callable(name: str, function: ActionHandler) -> type[BaseModel]:
    signature = inspect.signature(function)
    fields: dict[str, tuple[Any, Any]] = {}
    for parameter_name, parameter in signature.parameters.items():
        if parameter_name in {"browser_session", "session"}:
            continue
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue

        annotation = parameter.annotation
        if annotation is inspect.Parameter.empty:
            annotation = Any

        if parameter.default is inspect.Parameter.empty:
            default = ...
        else:
            default = parameter.default
        fields[parameter_name] = (annotation, default)

    model_name = "".join(part.capitalize() for part in name.split("_")) + "Params"
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _after_model_validator(function: Callable[[BaseModel], BaseModel]) -> classmethod:
    from pydantic import model_validator

    return model_validator(mode="after")(function)


__all__ = ["ActionHandler", "ActionRegistration", "ActionRegistry", "params_model_from_callable"]
